# -*- coding: utf-8 -*-
"""
txiptv 集成源转换脚本

将 txiptv 主机聚合 JSON 接口（如 https://iptvs.pes.im）转换为
本地频道源文件（默认 config/local/txiptv.txt），供 iptv-api 的
"本地源"机制使用，无需改动订阅源逻辑。

用法:
    python scripts/convert_txiptv.py
    python scripts/convert_txiptv.py --workers 80 --timeout 6
    python scripts/convert_txiptv.py --limit 30          # 仅测试前 30 个主机
"""

import argparse
import concurrent.futures
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

DEFAULT_API_URLS = [
    "https://iptvs.pes.im",
    "https://iptvs-speed.humorously.cn",
]
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "config" / "local" / "txiptv.txt"
PLAYLIST_PATH = "/iptv/live/1000.json"
DEFAULT_GROUP = "未分组"

session = requests.Session()


def fetch_host_list(api_urls, timeout):
    """从聚合接口拉取主机列表，多个地址按顺序尝试，取第一个成功的。"""
    for url in api_urls:
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            results = resp.json().get("results") or []
            links, seen = [], set()
            for item in results:
                if not isinstance(item, dict):
                    continue
                link = str(item.get("link") or "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                links.append(link)
            if links:
                print(f"[主机列表] {url}: {len(links)} 个主机")
                return links
            print(f"[主机列表] {url}: 响应中没有 results 数据")
        except Exception as exc:
            print(f"[主机列表] {url}: 获取失败 ({exc})")
    return []


def normalize_url(host, path):
    """把频道相对路径拼接为绝对地址。"""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return host.rstrip("/") + path


def fetch_playlist(host, timeout):
    """获取单个 txiptv 主机的频道列表，返回 (分组名, 频道名, 地址) 列表。"""
    url = host.rstrip("/") + PLAYLIST_PATH
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json().get("data") or []
    channels = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        raw = str(item.get("url") or "").strip()
        if not name or not raw:
            continue
        group = str(item.get("typename") or "").strip() or DEFAULT_GROUP
        channels.append((group, name, normalize_url(host, raw)))
    return channels


def write_output(output_path, groups):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 由 scripts/convert_txiptv.py 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    for group in sorted(groups):
        lines.append(f"{group},#genre#")
        for name, url in groups[group]:
            lines.append(f"{name},{url}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="将 txiptv 主机聚合 JSON 接口转换为 iptv-api 本地频道源文件"
    )
    parser.add_argument(
        "--url", action="append", dest="api_urls", default=None,
        help="聚合接口地址，可多次指定（默认: " + " ".join(DEFAULT_API_URLS) + "）",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出文件路径")
    parser.add_argument("--timeout", type=float, default=6.0, help="单个请求超时秒数")
    parser.add_argument("--workers", type=int, default=60, help="并发线程数")
    parser.add_argument("--limit", type=int, default=0, help="最多处理的主机数，0 表示全部（用于测试）")
    args = parser.parse_args()

    api_urls = args.api_urls or DEFAULT_API_URLS
    hosts = fetch_host_list(api_urls, args.timeout)
    if not hosts:
        print("未获取到任何主机，退出")
        return 1
    if args.limit > 0:
        hosts = hosts[: args.limit]

    total = len(hosts)
    print(f"开始获取频道列表: {total} 个主机, 并发 {args.workers}, 超时 {args.timeout}s")

    groups = {}
    seen = set()
    ok_hosts = failed_hosts = 0
    channel_total = 0
    done = 0

    def task(host):
        try:
            return host, fetch_playlist(host, args.timeout), None
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return host, None, True

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for _, channels, failed in pool.map(task, hosts):
            done += 1
            if failed:
                failed_hosts += 1
            elif channels:
                ok_hosts += 1
                for group, name, url in channels:
                    key = (name, url)
                    if key in seen:
                        continue
                    seen.add(key)
                    groups.setdefault(group, []).append((name, url))
                    channel_total += 1
            if done % 100 == 0 or done == total:
                print(f"[进度] {done}/{total} 主机, 可用 {ok_hosts}, 失败 {failed_hosts}, 频道 {channel_total}")

    if not channel_total:
        print("没有解析到任何频道，不写入文件")
        return 1

    write_output(args.output, groups)
    unique_names = len({name for items in groups.values() for name, _ in items})
    print(f"完成: 可用主机 {ok_hosts}/{total}, 频道 {channel_total} 条 (去重后 {unique_names} 个频道名)")
    print(f"已写入: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
