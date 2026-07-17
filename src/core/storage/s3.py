import fnmatch
import posixpath
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError

from src.core.storage.base import BaseStorageManager


class S3StorageManager(BaseStorageManager):
    def __init__(self, endpoint_url: str, access_key_id: str, secret_access_key: str, bucket_name: str):
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket_name = bucket_name
        
        # boto3 S3 클라이언트 초기화
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key
        )

    def _normalize_key(self, path: str) -> str:
        # 윈도우 스타일 백슬래시를 S3 슬래시로 변경하고, 맨 앞의 슬래시 제거
        key = path.replace("\\", "/")
        if key.startswith("/"):
            key = key[1:]
        return key

    def exists(self, path: str) -> bool:
        key = self._normalize_key(path)
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as e:
            # 404 Not Found일 경우 존재하지 않는 것으로 처리
            if e.response['Error']['Code'] == '404':
                # S3 폴더(가상 디렉토리)인 경우 접두사 매칭 테스트
                try:
                    res = self.s3_client.list_objects_v2(
                        Bucket=self.bucket_name,
                        Prefix=key + "/",
                        MaxKeys=1
                    )
                    return "Contents" in res
                except Exception:
                    return False
            return False

    def read_text(self, path: str) -> str:
        key = self._normalize_key(path)
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
            return response['Body'].read().decode('utf-8')
        except ClientError as e:
            raise FileNotFoundError(f"S3 Object not found: {key}. Error: {e}")

    def write_text(self, path: str, content: str) -> None:
        key = self._normalize_key(path)
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=content.encode('utf-8'),
            ContentType="text/markdown"
        )

    def read_bytes(self, path: str) -> bytes:
        key = self._normalize_key(path)
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
            return response["Body"].read()
        except ClientError as e:
            raise FileNotFoundError(f"S3 Object not found: {key}. Error: {e}") from e

    def write_bytes(self, path: str, content: bytes, content_type: Optional[str] = None) -> None:
        key = self._normalize_key(path)
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=content,
            ContentType=content_type or "application/octet-stream",
        )

    def list_files(self, target_dir: str, pattern: str = "*.md") -> List[str]:
        prefix = self._normalize_key(target_dir)
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
            
        files = []
        paginator = self.s3_client.get_paginator('list_objects_v2')
        
        try:
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                if "Contents" in page:
                    for obj in page["Contents"]:
                        key = obj["Key"]
                        # 디렉토리 플레이스홀더 오브젝트(끝이 /로 끝나는 객체)는 스킵
                        if key.endswith("/"):
                            continue
                            
                        # 패턴 대조 (예: glob *.md 형태)
                        filename = posixpath.basename(key)
                        if fnmatch.fnmatch(filename, pattern):
                            files.append(key)
        except Exception as e:
            print(f"Warning: Failed to list S3 objects for prefix '{prefix}': {e}")
            
        return files

    def copy_file(self, src_path: str, dest_path: str) -> None:
        dest_key = self._normalize_key(dest_path)
        
        src_key = self._normalize_key(src_path)
        copy_source = {'Bucket': self.bucket_name, 'Key': src_key}
        try:
            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket_name,
                Key=dest_key
            )
        except ClientError as e:
            raise FileNotFoundError(f"Failed to copy S3 object from {src_key} to {dest_key}: {e}")

    def delete_file(self, path: str) -> None:
        key = self._normalize_key(path)
        try:
            # 단일 파일 삭제
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
            
            # 가상 디렉토리 삭제 지원 (해당 경로를 prefix로 가지는 모든 객체 일괄 삭제)
            paginator = self.s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=key + "/"):
                if "Contents" in page:
                    delete_keys = [{'Key': obj['Key']} for obj in page['Contents']]
                    if delete_keys:
                        self.s3_client.delete_objects(
                            Bucket=self.bucket_name,
                            Delete={'Objects': delete_keys}
                        )
        except Exception as e:
            print(f"Warning: Failed to delete S3 path {key}: {e}")

    def makedirs(self, path: str) -> None:
        # S3는 디렉토리가 가상 개념이므로 생성 처리가 불필요합니다.
        pass
