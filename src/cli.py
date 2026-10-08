# src/cli.py
import argparse
from rich.console import Console
from rich.table import Table

from .downloader import BatchDownloader, TileDownloader
from .exceptions import OutputLockedError
from .providers import ProviderManager

console = Console()


def cmd_list_providers():
    table = Table(title="可用瓦片源")
    table.add_column("name", style="cyan")
    table.add_column("type")
    table.add_column("zoom_range")
    for name in ProviderManager.list_providers():
        p = ProviderManager.get_provider(name)
        table.add_row(name, p.provider_type.value, f"{p.min_zoom}-{p.max_zoom}")
    console.print(table)
    
def cmd_single(args):
    console.print("[bold blue]下载单一瓦片[/bold blue]")
    stats = BatchDownloader.download_single_tile(
        provider_name=args.provider,
        lat=args.lat,
        lon=args.lon,
        zoom=args.zoom,
        output_dir=args.output_dir,
        is_tms=args.tms,    # 关键
    )
    print_stats(stats)

def cmd_bbox(args):
    console.print("[bold blue]下载矩形区域瓦片[/bold blue]")
    stats = BatchDownloader.download_bbox(
        provider_name=args.provider,
        west=args.west,
        south=args.south,
        east=args.east,
        north=args.north,
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
        output_dir=args.output_dir,
        max_threads=args.threads,
        is_tms=args.tms,    # 关键
    )
    print_stats(stats)


def print_stats(stats: dict):
    table = Table(title="统计")
    for k in ["downloaded", "failed", "skipped", "total"]:
        table.add_row(k, str(stats.get(k, 0)))
    console.print(table)


def cmd_retry(args):
    """只重试进度库里失败过的瓦片（不重扫整个 bbox）。"""
    console.print("[bold blue]只重试失败瓦片[/bold blue]")
    downloader = TileDownloader(
        args.provider,
        args.output_dir,
        max_threads=args.threads,
        is_tms=args.tms,
    )
    try:
        count = downloader.add_failed_tasks()
        if count == 0:
            console.print("没有失败瓦片，无需重试")
            return
        console.print(f"发现 {count} 个失败瓦片，开始重试...")
        downloader.start()
        print_stats(downloader.get_statistics())
    finally:
        downloader.close()


def main():
    parser = argparse.ArgumentParser(description="地图瓦片下载器（类似 qmetatiles）")
    subparsers = parser.add_subparsers(dest="cmd")

    p_list = subparsers.add_parser("list", help="列出支持的瓦片源")

    p_single = subparsers.add_parser("single", help="下载单个瓦片")
    p_single.add_argument("--provider", required=True, help="瓦片源 (bing / custom 名称)")
    p_single.add_argument("--lat", type=float, required=True)
    p_single.add_argument("--lon", type=float, required=True)
    p_single.add_argument("--zoom", type=int, required=True)
    p_single.add_argument("--output-dir", default="tiles")
    p_single.add_argument("--tms", action="store_true", help="使用 TMS 模式")

    p_bbox = subparsers.add_parser("bbox", help="下载矩形区域瓦片")
    p_bbox.add_argument("--provider", required=True)
    p_bbox.add_argument("--north", type=float, required=True)
    p_bbox.add_argument("--south", type=float, required=True)
    p_bbox.add_argument("--west", type=float, required=True)
    p_bbox.add_argument("--east", type=float, required=True)
    p_bbox.add_argument("--min-zoom", type=int, required=True)
    p_bbox.add_argument("--max-zoom", type=int, required=True)
    p_bbox.add_argument("--output-dir", default="tiles")
    p_bbox.add_argument("--threads", default="auto",
                        help="下载线程数，正整数或 auto（默认自动）")
    p_bbox.add_argument("--tms", action="store_true", help="使用 TMS 模式")

    p_retry = subparsers.add_parser("retry", help="只重试上次失败的瓦片")
    p_retry.add_argument("--provider", required=True)
    p_retry.add_argument("--output-dir", required=True)
    p_retry.add_argument("--threads", default="auto",
                         help="下载线程数，正整数或 auto（默认自动）")
    p_retry.add_argument("--tms", action="store_true", help="使用 TMS 模式")

    args = parser.parse_args()

    try:
        if args.cmd == "list":
            cmd_list_providers()
        elif args.cmd == "single":
            cmd_single(args)
        elif args.cmd == "bbox":
            cmd_bbox(args)
        elif args.cmd == "retry":
            cmd_retry(args)
        else:
            parser.print_help()
    except OutputLockedError as e:
        console.print(f"[bold red]无法开始下载：[/bold red]{e}")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
