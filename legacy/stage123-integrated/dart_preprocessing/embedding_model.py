"""Local HuggingFace embeddings for the optional production Chroma index.

The official JSON E2E uses the CLOVA query embedding client in ``stage2/embedding.py``;
this module intentionally does not call the CLOVA Embedding API.
"""

from langchain_community.embeddings import HuggingFaceEmbeddings

# 로컬 임베딩 모델 로드 
embedding_model = HuggingFaceEmbeddings(
    model_name="jhgan/ko-sroberta-multitask",
    model_kwargs={'device': 'cuda'},  # GPU 사용 가능 시 'cuda'로 변경 / 불가시 'cpu'
    encode_kwargs={'normalize_embeddings': True}
)
