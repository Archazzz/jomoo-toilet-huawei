"""Local diagnostics CLI; private output files are never overwritten."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from bleak.exc import BleakError

from . import diagnostics


def save_private(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(content + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="九牧 249734 本地 BLE 诊断")
    parser.add_argument("--output", type=Path, help="新建私有 JSON 文件；推荐放在 work/ 下")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="扫描广播，不连接")
    inspect = commands.add_parser("inspect", help="枚举 GATT，不发送业务帧")
    inspect.add_argument("--device", required=True)
    query = commands.add_parser("query", help="发送一次候选状态查询，保留原始回包")
    query.add_argument("--device", required=True)
    query.add_argument("--characteristic", required=True, help="以 inspect 结果为准")
    query.add_argument("--write-mode", required=True, choices=["request", "command"])
    args = parser.parse_args()

    async def run():
        if args.command == "scan":
            return await diagnostics.scan()
        if args.command == "inspect":
            return await diagnostics.inspect(args.device)
        return await diagnostics.query(args.device, args.characteristic, args.write_mode)

    try:
        result = asyncio.run(run())
        content = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            save_private(args.output, content)
        else:
            print(content)
        return (
            2
            if isinstance(result, dict) and result.get("validated_status_received") is False
            else 0
        )
    except (diagnostics.DiagnosticError, BleakError, OSError, TimeoutError) as error:
        print(f"诊断失败：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
