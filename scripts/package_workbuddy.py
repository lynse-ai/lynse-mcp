"""Build the small remote OAuth connector ZIP accepted by WorkBuddy."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("connector-meta.json", "mcp.json", "icon.png")
MAX_BYTES = 20_000_000


def build_package(source: Path, output: Path, *, url: str | None = None) -> dict:
    metadata = json.loads((source / FILES[0]).read_text(encoding="utf-8"))
    config = json.loads((source / FILES[1]).read_text(encoding="utf-8"))
    if metadata.get("type") != "mcp" or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata.get("source", "")):
        raise ValueError("Invalid WorkBuddy connector type or source")
    for field in ("name", "name_en", "description", "description_zh", "description_en", "version", "minWorkbuddyVersion"):
        if not isinstance(metadata.get(field), str) or not metadata[field]:
            raise ValueError(f"Missing connector metadata: {field}")
    for field in ("examples_zh", "examples_en"):
        if not isinstance(metadata.get(field), list) or not metadata[field]:
            raise ValueError(f"Missing connector examples: {field}")
    servers = config.get("mcpServers", {})
    if len(servers) != 1:
        raise ValueError("WorkBuddy requires exactly one MCP server")
    server = next(iter(servers.values()))
    if url is not None:
        server["url"] = url
    if server.get("type") != "streamableHttp" or set(server) - {"type", "url", "timeout"}:
        raise ValueError("This package uses remote OAuth without embedded credentials or runtime")
    endpoint = urlsplit(server.get("url", ""))
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("The remote endpoint must be an HTTPS URL without credentials, query or fragment")
    payloads = {
        FILES[0]: (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode(),
        FILES[1]: (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode(),
        FILES[2]: (source / FILES[2]).read_bytes(),
    }
    unpacked = sum(len(data) for data in payloads.values())
    if unpacked >= MAX_BYTES:
        raise ValueError("Unpacked connector must be below 20 MB")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Explicit members prevent local secrets, caches and runtimes entering the ZIP.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in payloads.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    size = output.stat().st_size
    if size >= MAX_BYTES:
        output.unlink()
        raise ValueError("Connector ZIP must be below 20 MB")
    return {"path": str(output), "bytes": size, "unpacked_bytes": unpacked, "files": list(FILES), "url": server["url"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Override the default test URL with the deployed production HTTPS endpoint")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "lynse-workbuddy.zip")
    args = parser.parse_args()
    try:
        result = build_package(ROOT / "integrations" / "workbuddy", args.output.resolve(), url=args.url)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Cannot package WorkBuddy connector: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
