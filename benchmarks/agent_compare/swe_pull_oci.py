from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

REGISTRY = "https://registry-1.docker.io"
AUTH = "https://auth.docker.io/token"
MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull a Docker Hub image into a shared OCI layout with verified blobs"
    )
    parser.add_argument("image", help="Docker Hub image, optionally prefixed by docker.io/")
    parser.add_argument("--reference", default="latest")
    parser.add_argument("--tag", required=True, help="OCI layout tag")
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY"))
    parser.add_argument("--architecture", default="amd64")
    parser.add_argument("--os", dest="operating_system", default="linux")
    parser.add_argument("--import-podman", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository = normalize_repository(args.image)
    layout = args.layout.expanduser().resolve()
    initialize_layout(layout)
    token = registry_token(repository, args.proxy)
    tagged_raw = fetch_manifest(repository, args.reference, token, args.proxy)
    tagged = json.loads(tagged_raw)
    media_type = str(tagged.get("mediaType", ""))
    if media_type in INDEX_MEDIA_TYPES or "manifests" in tagged:
        descriptor = select_platform(
            tagged,
            architecture=args.architecture,
            operating_system=args.operating_system,
        )
        manifest_digest = str(descriptor["digest"])
        manifest_raw = fetch_manifest(repository, manifest_digest, token, args.proxy)
        expected_size = int(descriptor.get("size", len(manifest_raw)))
    else:
        manifest_raw = tagged_raw
        manifest_digest = digest_bytes(manifest_raw)
        expected_size = len(manifest_raw)
    verify_digest(manifest_raw, manifest_digest, "manifest")
    if len(manifest_raw) != expected_size:
        raise RuntimeError(
            f"manifest size mismatch: expected {expected_size}, got {len(manifest_raw)}"
        )
    manifest = json.loads(manifest_raw)
    manifest_media_type = str(
        manifest.get("mediaType", "application/vnd.oci.image.manifest.v1+json")
    )
    store_blob(layout, manifest_digest, manifest_raw)

    descriptors = [manifest["config"], *manifest.get("layers", [])]
    total = sum(int(item.get("size", 0)) for item in descriptors)
    print(
        f"IMAGE {repository}:{args.reference} platform={args.operating_system}/{args.architecture} "
        f"blobs={len(descriptors)} compressed={total / 1073741824:.2f}GiB",
        flush=True,
    )
    for index, descriptor in enumerate(descriptors, start=1):
        digest = str(descriptor["digest"])
        size = int(descriptor.get("size", 0))
        target = blob_path(layout, digest)
        if valid_file(target, digest, size):
            print(f"  [{index}/{len(descriptors)}] CACHED {digest} {size}", flush=True)
            continue
        print(
            f"  [{index}/{len(descriptors)}] FETCH {digest} {size / 1048576:.1f}MiB",
            flush=True,
        )
        download_blob(repository, digest, target, token, args.proxy)
        if not valid_file(target, digest, size):
            actual_size = target.stat().st_size if target.exists() else -1
            actual_digest = digest_file(target) if target.exists() else "missing"
            target.unlink(missing_ok=True)
            raise RuntimeError(
                f"blob verification failed for {digest}: size={actual_size}, digest={actual_digest}"
            )

    update_index(
        layout,
        tag=args.tag,
        digest=manifest_digest,
        size=len(manifest_raw),
        media_type=manifest_media_type,
    )
    print(f"OCI_READY {layout}:{args.tag} digest={manifest_digest}", flush=True)
    if args.import_podman:
        destination = f"docker.io/{repository}:{args.reference}"
        subprocess.run(
            [
                "skopeo",
                "copy",
                f"oci:{layout}:{args.tag}",
                f"containers-storage:{destination}",
            ],
            check=True,
        )
        print(f"PODMAN_READY {destination}", flush=True)
    return 0


def normalize_repository(value: str) -> str:
    repository = value.removeprefix("docker://").removeprefix("docker.io/")
    if ":" in repository.rsplit("/", 1)[-1]:
        repository = repository.rsplit(":", 1)[0]
    if "/" not in repository:
        repository = f"library/{repository}"
    return repository


def registry_token(repository: str, proxy: str | None) -> str:
    query = urlencode(
        {
            "service": "registry.docker.io",
            "scope": f"repository:{repository}:pull",
        }
    )
    value = json.loads(fetch_bytes(f"{AUTH}?{query}", proxy=proxy))
    token = value.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Docker Hub did not return an access token")
    return token


def fetch_manifest(
    repository: str,
    reference: str,
    token: str,
    proxy: str | None,
) -> bytes:
    return fetch_bytes(
        f"{REGISTRY}/v2/{repository}/manifests/{reference}",
        proxy=proxy,
        headers=(
            f"Authorization: Bearer {token}",
            f"Accept: {MANIFEST_ACCEPT}",
        ),
    )


def fetch_bytes(
    url: str,
    *,
    proxy: str | None,
    headers: tuple[str, ...] = (),
) -> bytes:
    command = [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--retry",
        "8",
        "--retry-delay",
        "1",
        "--retry-all-errors",
        "--connect-timeout",
        "15",
        "--max-time",
        "180",
    ]
    if proxy:
        command.extend(["--proxy", proxy])
    for header in headers:
        command.extend(["--header", header])
    command.append(url)
    return subprocess.check_output(command)


def select_platform(
    index: dict[str, Any],
    *,
    architecture: str,
    operating_system: str,
) -> dict[str, Any]:
    candidates = [
        item
        for item in index.get("manifests", [])
        if isinstance(item, dict)
        and isinstance(item.get("platform"), dict)
        and item["platform"].get("architecture") == architecture
        and item["platform"].get("os") == operating_system
    ]
    if not candidates:
        raise RuntimeError(f"manifest list has no {operating_system}/{architecture} descriptor")
    candidates.sort(
        key=lambda item: bool(item.get("platform", {}).get("variant")),
    )
    return candidates[0]


def download_blob(
    repository: str,
    digest: str,
    target: Path,
    token: str,
    proxy: str | None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    temporary.unlink(missing_ok=True)
    command = [
        "curl",
        "--fail",
        "--location",
        "--retry",
        "15",
        "--retry-delay",
        "1",
        "--retry-all-errors",
        "--connect-timeout",
        "15",
        "--max-time",
        "900",
        "--progress-bar",
        "--header",
        f"Authorization: Bearer {token}",
        "--output",
        str(temporary),
    ]
    if proxy:
        command.extend(["--proxy", proxy])
    command.append(f"{REGISTRY}/v2/{repository}/blobs/{digest}")
    subprocess.run(command, check=True)
    temporary.replace(target)


def initialize_layout(layout: Path) -> None:
    (layout / "blobs" / "sha256").mkdir(parents=True, exist_ok=True)
    layout_file = layout / "oci-layout"
    if not layout_file.exists():
        layout_file.write_text(
            json.dumps({"imageLayoutVersion": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
    index = layout / "index.json"
    if not index.exists():
        index.write_text(
            json.dumps({"schemaVersion": 2, "manifests": []}) + "\n",
            encoding="utf-8",
        )


def update_index(
    layout: Path,
    *,
    tag: str,
    digest: str,
    size: int,
    media_type: str,
) -> None:
    path = layout / "index.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    manifests = [
        item
        for item in value.get("manifests", [])
        if item.get("annotations", {}).get("org.opencontainers.image.ref.name") != tag
    ]
    manifests.append(
        {
            "mediaType": media_type,
            "digest": digest,
            "size": size,
            "annotations": {"org.opencontainers.image.ref.name": tag},
        }
    )
    value["schemaVersion"] = 2
    value["manifests"] = manifests
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def store_blob(layout: Path, digest: str, data: bytes) -> None:
    target = blob_path(layout, digest)
    if target.exists() and valid_file(target, digest, len(data)):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(data)
    verify_digest(data, digest, "blob")
    temporary.replace(target)


def blob_path(layout: Path, digest: str) -> Path:
    algorithm, value = digest.split(":", 1)
    if algorithm != "sha256" or not value:
        raise ValueError(f"unsupported digest: {digest}")
    return layout / "blobs" / algorithm / value


def valid_file(path: Path, digest: str, size: int) -> bool:
    return path.is_file() and path.stat().st_size == size and digest_file(path) == digest


def digest_file(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            checksum.update(chunk)
    return f"sha256:{checksum.hexdigest()}"


def digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def verify_digest(value: bytes, expected: str, label: str) -> None:
    actual = digest_bytes(value)
    if actual != expected:
        raise RuntimeError(f"{label} digest mismatch: expected {expected}, got {actual}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"command failed with exit code {exc.returncode}", file=sys.stderr)
        raise
