import glob
import os
import shutil
from typing import List

from src.core.storage.base import BaseStorageManager


class LocalStorageManager(BaseStorageManager):
    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)

    def _full_path(self, path: str) -> str:
        # 이미 절대 경로라면 그대로 반환, 아니면 root_dir와 결합
        if os.path.isabs(path):
            return path
        return os.path.join(self.root_dir, path)

    def exists(self, path: str) -> bool:
        return os.path.exists(self._full_path(path))

    def read_text(self, path: str) -> str:
        with open(self._full_path(path), 'r', encoding='utf-8') as f:
            return f.read()

    def write_text(self, path: str, content: str) -> None:
        full_path = self._full_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def list_files(self, target_dir: str, pattern: str = "*.md") -> List[str]:
        full_target = self._full_path(target_dir)
        if not os.path.exists(full_target):
            return []
        
        search_pattern = os.path.join(full_target, "**", pattern)
        files = []
        for full_path in glob.glob(search_pattern, recursive=True):
            if os.path.isfile(full_path):
                # root_dir 기준 상대경로로 리턴
                rel_path = os.path.relpath(full_path, self.root_dir)
                files.append(rel_path)
        return files

    def copy_file(self, src_path: str, dest_path: str) -> None:
        full_src = self._full_path(src_path)
        full_dest = self._full_path(dest_path)
        
        if not os.path.exists(full_src):
            raise FileNotFoundError(f"Source file not found: {full_src}")
            
        os.makedirs(os.path.dirname(full_dest), exist_ok=True)
        shutil.copy(full_src, full_dest)

    def delete_file(self, path: str) -> None:
        full_path = self._full_path(path)
        if os.path.exists(full_path):
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)

    def makedirs(self, path: str) -> None:
        os.makedirs(self._full_path(path), exist_ok=True)
