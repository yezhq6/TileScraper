#!/usr/bin/env python3
"""确定性复现：有界任务队列被打满时，pause() 是否导致永久死锁。

用法: ts_deadlock.py <queue_size> <mode>
  mode=normal   pause -> resume，观察能否跑完
  mode=cancel   pause 后卡死，再用 cancel() 看能否解锁
"""

import faulthandler
import http.server
import os
import sys
import threading
import time
from pathlib import Path

PROJ = str(Path(__file__).resolve().parents[2])  # 仓库根目录（本脚本位于 tools/stress/）
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="ERROR", format="{message}")

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)


class _H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        time.sleep(self.server.delay)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
        except (BrokenPipeError, ConnectionResetError):
            pass


class _S(http.server.ThreadingHTTPServer):
    request_queue_size = 512


DOC = "http://127.0.0.1:%d/{z}/{x}/{y}.png"


def count(out):
    return sum(1 for _ in Path(out).rglob("*.png"))


def worker_states(d):
    st = {}
    for t in threading.enumerate():
        st[t.name] = t.is_alive()
    return st


def main():
    qsize = int(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "normal"
    out = Path(f"/tmp/ts_dl_{qsize}_{mode}")
    if out.exists():
        import shutil
        shutil.rmtree(out)

    httpd = _S(("127.0.0.1", 0), _H)
    httpd.daemon_threads = True
    httpd.delay = 0.01
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    from src.config import config_manager
    from src.providers import ProviderManager
    from src.downloader.base import TileDownloader
    from src.downloader.request import RequestSessionManager

    orig = RequestSessionManager.create_session

    def patched(self):
        s = orig(self)
        s.trust_env = False
        return s

    RequestSessionManager.create_session = patched

    config_manager.config.setdefault("download", {})["task_queue_size"] = qsize
    ProviderManager.create_custom_provider("dl_test", DOC % port)
    d = TileDownloader("dl_test", str(out), max_threads=32, enable_resume=True,
                       save_format="directory")
    d.add_tasks_for_bbox(115.0, 39.0, 117.0, 41.0, 12, 14)
    print(f"queue_size={qsize} total={d.total_tasks} mode={mode}", flush=True)

    th = threading.Thread(target=d.start, daemon=True, name="SessionStart")
    th.start()
    time.sleep(1.5)
    print(f"  pause: 已下载={count(out)}", flush=True)
    d.pause()
    time.sleep(1.5)
    n_paused = count(out)
    print(f"  paused: 文件={n_paused} qsize={d.task_queue.qsize()}/{d.task_queue.maxsize} "
          f"unfinished={d.task_queue.unfinished_tasks}", flush=True)
    d.resume()

    # 观察 12 秒，看是否继续推进
    prev = n_paused
    for i in range(4):
        time.sleep(3)
        cur = count(out)
        print(f"  +{(i+1)*3}s: 文件={cur} qsize={d.task_queue.qsize()}/"
              f"{d.task_queue.maxsize} start_返回={not th.is_alive()}", flush=True)
        if th.is_alive() and cur == prev and not d.task_queue.empty():
            prev = cur
        else:
            prev = cur

    if th.is_alive():
        print("  >>> 仍在运行（可能死锁）", flush=True)
        print("  --- 线程栈 ---", flush=True)
        faulthandler.dump_traceback(file=sys.stderr)
        print("  --- 线程状态 ---", flush=True)
        for n, a in worker_states(d).items():
            print(f"      {n}: alive={a}", flush=True)
        print(f"  统计={d.get_statistics()} qsize={d.task_queue.qsize()} "
              f"all_tasks_added={d.all_tasks_added.is_set()}", flush=True)

        if mode == "cancel":
            print("  >>> 调用 cancel()", flush=True)
            d.cancel()
            th.join(timeout=30)
            print(f"  cancel 后: start返回={not th.is_alive()} 文件={count(out)} "
                  f"统计={d.get_statistics()}", flush=True)
    else:
        print(f"  >>> 正常结束: 文件={count(out)} 统计={d.get_statistics()}", flush=True)

    httpd.shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()
