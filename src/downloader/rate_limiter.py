# src/downloader/rate_limiter.py

"""限流自适应：可动态收缩的并发闸门 + 全局冷却。

背景：遇到 429 / 503 时，旧实现只做固定指数退避，不解析 ``Retry-After``，
也不会降低并发，于是所有线程会一起继续猛冲，服务端持续拒绝。

这里提供一个进程内共享的 :class:`ConcurrencyLimiter`：

* **并发闸门**：工作线程每处理一个瓦片前先 ``acquire()``，把实际并发钳制
  在 ``limit`` 以内（初始等于线程数，因此默认行为与过去一致）；
* **收到限流信号**：``note_throttled()`` 设置一段全局冷却时间（所有线程
  一起等待，而不是各退各的），并按比例收缩 ``limit``；
* **恢复**：冷却结束且连续成功达到阈值后，逐步把 ``limit`` 加回上限。

这样既避免"限流时还在加并发"，也不会永久性地把并发压在低位。
"""

import threading
import time
from email.utils import parsedate_to_datetime
from datetime import timezone

from loguru import logger


def parse_retry_after(value) -> float:
    """
    解析 HTTP ``Retry-After`` 头，返回需要等待的秒数；无法解析时返回 None。

    支持两种合法形式：秒数（``120``）与 HTTP 日期（``Wed, 21 Oct 2015 07:28:00 GMT``）。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    try:
        seconds = float(text)
    except ValueError:
        seconds = None
    if seconds is not None:
        return max(0.0, seconds)

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - time.time())


class ConcurrencyLimiter:
    """动态并发闸门 + 全局限流冷却（线程安全）。"""

    def __init__(
        self,
        limit: int,
        min_limit: int = 1,
        cooldown_max: float = 60.0,
        base_cooldown: float = 1.0,
        shrink_factor: float = 0.75,
        shrink_interval: float = 2.0,
        recover_interval: float = 5.0,
        recover_successes: int = 20,
    ):
        """
        Args:
            limit: 初始并发上限（通常等于工作线程数），同时也是回升的天花板
            min_limit: 收缩下限，不会低于此值
            cooldown_max: 单次 ``Retry-After`` 冷却时间的上限（秒）
            base_cooldown: 未提供 ``Retry-After`` 时的默认冷却（秒）
            shrink_factor: 每次限流事件把并发乘以的系数
            shrink_interval: 两次收缩之间的最小间隔（秒），避免一次抖动就砍到最低
            recover_interval: 两次恢复之间的最小间隔（秒）
            recover_successes: 冷却结束后需要连续成功多少次才尝试恢复 1 个并发
        """
        initial = max(1, int(limit))
        self._max_limit = initial
        self._min_limit = max(1, min(int(min_limit), initial))
        self._limit = initial
        self._active = 0

        self._cooldown_max = max(1.0, float(cooldown_max))
        self._base_cooldown = max(0.0, float(base_cooldown))
        self._shrink_factor = min(0.99, max(0.1, float(shrink_factor)))
        self._shrink_interval = max(0.0, float(shrink_interval))
        self._recover_interval = max(0.0, float(recover_interval))
        self._recover_successes = max(1, int(recover_successes))

        self._cond = threading.Condition()
        self._cooldown_until = 0.0
        self._last_shrink = 0.0
        self._last_recover = time.time()
        self._success_streak = 0
        self._throttle_events = 0

    # ------------------------------------------------------------------ #
    # 只读属性
    # ------------------------------------------------------------------ #
    @property
    def limit(self) -> int:
        with self._cond:
            return self._limit

    @property
    def max_limit(self) -> int:
        return self._max_limit

    @property
    def active(self) -> int:
        with self._cond:
            return self._active

    @property
    def throttle_events(self) -> int:
        with self._cond:
            return self._throttle_events

    def cooldown_remaining(self) -> float:
        with self._cond:
            return max(0.0, self._cooldown_until - time.time())

    def set_limit(self, value) -> int:
        """
        外部直接设置并发上限（供实验/诊断脚本使用，运行时控制器暂未内置）。

        钳制在 ``[min_limit, max_limit]``；返回实际生效值。
        """
        try:
            target = int(value)
        except (TypeError, ValueError):
            return self.limit
        with self._cond:
            target = max(self._min_limit, min(target, self._max_limit))
            if target != self._limit:
                logger.debug(f"并发上限调整: {self._limit} → {target}")
                self._limit = target
                self._cond.notify_all()
            return self._limit

    # ------------------------------------------------------------------ #
    # 闸门
    # ------------------------------------------------------------------ #
    def acquire(self, stop_event=None, timeout: float = 0.2) -> bool:
        """
        获取一个并发槽位。

        Returns:
            True 已获得（必须配对调用 :meth:`release`）；
            False 等待期间 ``stop_event`` 被置位，调用方应放弃当前任务。
        """
        with self._cond:
            while self._active >= self._limit:
                self._cond.wait(timeout)
                if stop_event is not None and stop_event.is_set():
                    return False
            self._active += 1
            return True

    def release(self):
        """归还并发槽位。"""
        with self._cond:
            if self._active > 0:
                self._active -= 1
            self._cond.notify_all()

    # ------------------------------------------------------------------ #
    # 限流 / 恢复
    # ------------------------------------------------------------------ #
    def note_throttled(self, retry_after=None) -> float:
        """
        记录一次服务端限流（429 / 503），返回本次全局冷却秒数。

        ``retry_after`` 可以是已解析好的秒数，也可以是 ``Retry-After`` 头原文；
        ``None`` 表示头缺失，此时使用默认冷却。``0`` 表示服务端明确要求不等待。
        """
        if isinstance(retry_after, str):
            retry_after = parse_retry_after(retry_after)
        if retry_after is None:
            delay = self._base_cooldown
        else:
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = self._base_cooldown
        delay = max(0.0, min(delay, self._cooldown_max))

        now = time.time()
        with self._cond:
            self._throttle_events += 1
            self._success_streak = 0
            self._cooldown_until = max(self._cooldown_until, now + delay)

            if (
                self._limit > self._min_limit
                and now - self._last_shrink >= self._shrink_interval
            ):
                new_limit = max(self._min_limit, int(self._limit * self._shrink_factor))
                if new_limit < self._limit:
                    logger.warning(
                        f"检测到限流，并发上限 {self._limit} → {new_limit}"
                        f"（冷却 {delay:.1f}s）"
                    )
                    self._limit = new_limit
                    self._last_shrink = now
            self._cond.notify_all()
        return delay

    def note_success(self):
        """记录一次成功；满足条件时把并发上限逐步恢复。"""
        now = time.time()
        with self._cond:
            if self._cooldown_until > now:
                return
            self._success_streak += 1
            if self._limit >= self._max_limit:
                return
            if self._success_streak < self._recover_successes:
                return
            if now - self._last_recover < self._recover_interval:
                return
            self._limit += 1
            self._success_streak = 0
            self._last_recover = now
            logger.info(f"连续成功后恢复并发上限至 {self._limit}")
            self._cond.notify_all()

    def wait_for_cooldown(self, stop_event=None, poll: float = 0.2) -> bool:
        """
        阻塞等待全局限流冷却结束。

        Returns:
            True 可以继续；False 等待期间 ``stop_event`` 被置位。
        """
        while True:
            with self._cond:
                remaining = self._cooldown_until - time.time()
            if remaining <= 0:
                return True
            if stop_event is not None and stop_event.is_set():
                return False
            time.sleep(min(poll, remaining))
