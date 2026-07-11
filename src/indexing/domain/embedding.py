import os
import hashlib
import random
from abc import ABC, abstractmethod
from typing import List, Optional



class BaseEmbeddingService(ABC):
    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """
        주어진 텍스트에 대한 임베딩 벡터를 리턴합니다.
        """
        pass

    def embed_batch(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """
        여러 텍스트를 배치로 임베딩합니다.
        기본 구현은 개별 embed_text 반복이며, 하위 클래스에서 네이티브 배치 API로 오버라이드합니다.
        """
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            for text in batch:
                results.append(self.embed_text(text))
        return results

    @abstractmethod
    def get_dimension(self) -> int:
        """
        임베딩 벡터의 차원을 리턴합니다.
        """
        pass

class FakeEmbeddingService(BaseEmbeddingService):
    """
    개발 및 테스트를 위해 텍스트의 해시값을 기반으로
    결정론적인(L2 정규화된) 더미 임베딩 벡터를 리턴합니다.
    """
    def __init__(self, dimension: int = 1536):
        self.dimension = dimension

    def embed_text(self, text: str) -> List[float]:
        hash_seed = int(hashlib.md5(text.encode('utf-8')).hexdigest(), 16)
        rng = random.Random(hash_seed)
        
        vector = [rng.uniform(-1.0, 1.0) for _ in range(self.dimension)]
        norm = sum(x**2 for x in vector) ** 0.5
        if norm > 0:
            vector = [x / norm for x in vector]
            
        return vector

    def get_dimension(self) -> int:
        return self.dimension

class OpenAIEmbeddingService(BaseEmbeddingService):
    """
    OpenAI Embedding API를 사용하는 임베딩 서비스
    """
    def __init__(self, model_name: str = "text-embedding-3-small", dimension: int = 1536, api_key: Optional[str] = None):
        self.model_name = model_name
        self.dimension = dimension
        
        # lazy import to avoid start-up overhead
        from openai import OpenAI
        
        # 1. 인자로 넘어온 api_key가 없으면, 유저 컨텍스트를 활용해 로드 (DB 의존성 배제)
        if not api_key:
            try:
                from src.core.config import current_user_config
                config = current_user_config.get() or {}
                api_key = config.get("openai_api_key")
            except Exception:
                pass
                
        # 2. 컨텍스트에도 없으면 전역 환경변수(os.getenv) 폴백 (로컬 구동 하위 호환성)
        if not api_key:
            api_key = os.getenv("OPENAI_API_KEY")
            
        # 3. 여전히 키가 없다면 에러 발생
        if not api_key:
            raise ValueError(
                "OpenAI API Key가 설정되지 않았거나 유효하지 않습니다. "
                "통합인증 및 에이전트 설정(mcp.json)에 OpenAI API Key를 주입해 주세요."
            )
            
        self.client = OpenAI(api_key=api_key)


    def embed_text(self, text: str) -> List[float]:
        # text-embedding-3 모델들은 차원 지정을 지원합니다.
        kwargs = {"input": [text], "model": self.model_name}
        if "text-embedding-3" in self.model_name:
            kwargs["dimensions"] = self.dimension
            
        response = self.client.embeddings.create(**kwargs)
        return response.data[0].embedding

    def embed_batch(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """
        OpenAI 배치 임베딩 API를 활용하여 한 번의 호출로 여러 텍스트를 임베딩합니다.
        batch_size 단위로 청킹하여 과도한 페이로드를 방지합니다.
        """
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            kwargs = {"input": batch, "model": self.model_name}
            if "text-embedding-3" in self.model_name:
                kwargs["dimensions"] = self.dimension

            response = self.client.embeddings.create(**kwargs)
            # API 응답의 index 순서가 보장되지 않을 수 있으므로 정렬
            batch_embeddings = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
            all_embeddings.extend(batch_embeddings)
        return all_embeddings

    def get_dimension(self) -> int:
        return self.dimension

class BGEM3EmbeddingService(BaseEmbeddingService):
    """
    BAAI/bge-m3 모델을 로컬에서 실행하는 임베딩 서비스
    """
    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name
        
        # lazy import
        print(f"Loading local embedding model '{model_name}' (this may take a few seconds)...")
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        # bge-m3의 기본 Dense 임베딩 차원은 1024입니다.
        self.dimension = 1024

    def embed_text(self, text: str) -> List[float]:
        # 로컬 장치(Apple Silicon의 경우 MPS, 일반 CPU)에서 인코딩 실행
        embeddings = self.model.encode(text, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_batch(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """
        SentenceTransformer의 네이티브 배치 encode를 활용합니다.
        GPU/MPS 메모리 관리를 위해 batch_size 단위로 청킹합니다.
        """
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            embeddings = self.model.encode(batch, normalize_embeddings=True)
            all_embeddings.extend(embeddings.tolist())
        return all_embeddings

    def get_dimension(self) -> int:
        return self.dimension
