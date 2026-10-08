#!/usr/bin/env python3
"""清理输出目录里残留的 ``.<瓦片名>.<随机>.part`` 临时文件。

强杀/断电时 ``os.replace`` 没来得及执行，会留下这些临时文件。下载器在重新
下载对应瓦片时会自动清理（见 ``worker._cleanup_stale_parts``），这个脚本用于
**批量**清理历史遗留（例如换机器、换数据集之后）。

用法::

    python tools/clean_parts.py /path/to/tiles            # 全部清理
    python tools/clean_parts.py /path/to/tiles --hours 24 # 只清理 24 小时前的
    python tools/clean_parts.py /path/to/tiles --dry-run  # 只统计不删除
"""

import argparse
import os
import sys
import time
from pathlib import Path


def find_parts(root: Path, older_than_seconds=None):
    cutoff = None if older_than_seconds is None else time.time() - older_than_seconds
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not (name.startswith(".") and name.endswith(".part")):
                continue
            path = Path(dirpath) / name
            if cutoff is not None:
                try:
                    if path.stat().st_mtime > cutoff:
                        continue
                except OSError:
                    continue
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="清理残留的 .part 临时瓦片文件")
    parser.add_argument("path", help="输出目录（目录模式）")
    parser.add_argument(
        "--hours", type=float, default=0.0,
        help="只删除 mtime 超过这么多小时的临时文件（0 = 全部，默认 0）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不删除")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"路径不存在: {root}")
        return 1

    older = args.hours * 3600 if args.hours > 0 else None
    count = 0
    freed = 0
    for path in find_parts(root, older):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        print(f"[{'DRY' if args.dry_run else 'DEL'}] {path} ({size} bytes)")
        if not args.dry_run:
            try:
                path.unlink()
            except OSError as e:  # noqa: PERF203
                print(f"  删除失败: {e}")
                continue
        count += 1
        freed += size

    action = "预计释放" if args.dry_run else "释放"
    print(f"共 {count} 个文件，{action} {freed / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
