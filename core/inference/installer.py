"""Automatic installation of official llama.cpp release binaries."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import shutil
import tempfile
import time
from typing import Callable
from urllib.request import Request, urlopen
import zipfile

from core.inference.manager import LlamaVersionStore


GITHUB_LATEST_RELEASE = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int


@dataclass(frozen=True)
class LlamaRelease:
    version: str
    assets: tuple[ReleaseAsset, ...]


def fetch_latest_release(timeout: float = 30.0) -> LlamaRelease:
    request = Request(
        GITHUB_LATEST_RELEASE,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "TagManager"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed URL
        payload = json.load(response)
    assets = tuple(
        ReleaseAsset(item["name"], item["browser_download_url"], int(item["size"]))
        for item in payload.get("assets", [])
        if item.get("name") and item.get("browser_download_url")
    )
    version = str(payload.get("tag_name", "")).strip()
    if not version or not assets:
        raise RuntimeError("GitHub вернул неполное описание релиза llama.cpp")
    return LlamaRelease(version, assets)


def select_windows_assets(
    release: LlamaRelease, backend: str = "cuda-12.4"
) -> tuple[ReleaseAsset, ...]:
    suffix = f"bin-win-{backend}-x64.zip"
    main = next(
        (asset for asset in release.assets
         if asset.name.startswith(f"llama-{release.version}-") and asset.name.endswith(suffix)),
        None,
    )
    if main is None:
        # Release tag and archive prefix occasionally differ; suffix is stable.
        main = next(
            (asset for asset in release.assets
             if asset.name.startswith("llama-") and asset.name.endswith(suffix)),
            None,
        )
    if main is None:
        raise RuntimeError(f"В релизе {release.version} нет Windows {backend} build")
    selected = [main]
    if backend.startswith("cuda-"):
        runtime_name = f"cudart-llama-bin-win-{backend}-x64.zip"
        runtime = next((asset for asset in release.assets if asset.name == runtime_name), None)
        if runtime is None:
            raise RuntimeError(
                f"В релизе {release.version} отсутствует CUDA runtime {backend}"
            )
        selected.append(runtime)
    return tuple(selected)


class LlamaCppInstaller:
    def __init__(self, root: str, backend: str = "cuda-12.4"):
        self.store = LlamaVersionStore(root)
        self.backend = backend

    def current_executable(self) -> str | None:
        version = self.store.current()
        if not version:
            return None
        payload = os.path.join(self.store.versions_dir, version, "payload")
        for root, _, files in os.walk(payload):
            for name in files:
                if name.lower() == "llama-server.exe":
                    return os.path.join(root, name)
        return None

    def ensure_latest(
        self,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> str:
        release = fetch_latest_release()
        current = self.store.current()
        if current == release.version and self.store.verify(current):
            executable = self.current_executable()
            if executable:
                return executable

        assets = select_windows_assets(release, self.backend)
        if os.path.isdir(os.path.join(self.store.versions_dir, release.version)):
            if not self.store.verify(release.version):
                raise RuntimeError(
                    f"Установленная версия {release.version} повреждена; "
                    "удалите её через менеджер версий перед повторной установкой"
                )
        else:
            self._download_and_install(release.version, assets, progress)
        self.store.activate(release.version)
        executable = self.current_executable()
        if not executable:
            raise RuntimeError("В официальном архиве не найден llama-server.exe")
        return executable

    def _download_and_install(
        self,
        version: str,
        assets: tuple[ReleaseAsset, ...],
        progress: Callable[[int, int, str], None] | None,
    ) -> None:
        total = sum(asset.size for asset in assets)
        completed = 0
        download_dir = os.path.join(self.store.root, "downloads", version)
        os.makedirs(download_dir, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tagmanager-llama-") as temp:
            merged = os.path.join(temp, "merged")
            os.mkdir(merged)
            download_hashes: dict[str, str] = {}
            for asset in assets:
                archive = os.path.join(download_dir, asset.name + ".part")

                def report(asset_done: int, *, name=asset.name, base=completed):
                    if progress:
                        progress(base + asset_done, total, name)

                download_hashes[asset.name] = self._download(asset, archive, report)
                self._extract_zip_safe(archive, merged)
                completed += asset.size
                if progress:
                    progress(completed, total, asset.name)
            with open(os.path.join(merged, "downloads.sha256.json"), "w",
                      encoding="utf-8") as stream:
                json.dump(download_hashes, stream, indent=2)
            self.store.install(version, merged)
        shutil.rmtree(download_dir, ignore_errors=True)

    @staticmethod
    def _download(
        asset: ReleaseAsset,
        target: str,
        progress: Callable[[int], None] | None,
    ) -> str:
        max_retries = 12
        downloaded = os.path.getsize(target) if os.path.isfile(target) else 0
        if downloaded > asset.size:
            os.remove(target)
            downloaded = 0

        retries = 0
        while downloaded < asset.size:
            headers = {"User-Agent": "TagManager"}
            if downloaded:
                headers["Range"] = f"bytes={downloaded}-"
            request = Request(asset.url, headers=headers)
            try:
                with urlopen(request, timeout=60.0) as response:  # noqa: S310
                    status = getattr(response, "status", response.getcode())
                    content_range = response.headers.get("Content-Range", "")
                    if downloaded and status != 206 and not content_range.startswith(
                        f"bytes {downloaded}-"
                    ):
                        # CDN ignored Range: restart safely instead of appending a full
                        # archive to the partial file.
                        downloaded = 0
                        with open(target, "wb"):
                            pass
                    mode = "ab" if downloaded else "wb"
                    with open(target, mode) as stream:
                        while downloaded < asset.size:
                            block = response.read(min(1024 * 1024, asset.size - downloaded))
                            if not block:
                                break
                            stream.write(block)
                            downloaded += len(block)
                            if progress:
                                progress(downloaded)
                        stream.flush()
                        os.fsync(stream.fileno())
                if downloaded < asset.size:
                    raise ConnectionError(
                        f"соединение оборвалось на {downloaded}/{asset.size} байт"
                    )
                retries = 0
            except Exception as exc:  # noqa: BLE001
                retries += 1
                if retries >= max_retries:
                    raise RuntimeError(
                        f"Не удалось докачать {asset.name} после {max_retries} попыток: {exc}"
                    ) from exc
                time.sleep(min(30.0, 2 ** (retries - 1)))
                downloaded = os.path.getsize(target) if os.path.isfile(target) else 0

        digest = hashlib.sha256()
        with open(target, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _extract_zip_safe(archive: str, destination: str) -> None:
        destination_abs = os.path.abspath(destination)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = os.path.abspath(os.path.join(destination, member.filename))
                if os.path.commonpath([destination_abs, target]) != destination_abs:
                    raise RuntimeError(f"Небезопасный путь в архиве: {member.filename}")
            bundle.extractall(destination)


def discover_gguf(folder: str) -> list[str]:
    if not folder or not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".gguf") and os.path.isfile(os.path.join(folder, name))
    )
