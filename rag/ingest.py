
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document


load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "data" / "documents"
CHROMA_DIR = ROOT / "data" / "chroma_db"

COLLECTION_NAME = "callcenter_policies"


def get_embeddings():
    return OllamaEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text"),
        base_url=os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        ),
    )


def ingest_documents():
    documents = []

    for path in sorted(DOCS_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8")

        documents.append(
            Document(
                page_content=content,
                metadata={"source": path.name},
            )
        )

    if not documents:
        raise RuntimeError("Dokuman bulunamadi.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=700,
        chunk_overlap=100,
    )

    chunks = splitter.split_documents(documents)

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=str(CHROMA_DIR),
    )

    # Tekrar calistirildiginda ayni chunk'lari cogaltma.
    ids = [
        f"{chunk.metadata['source']}:{i}"
        for i, chunk in enumerate(chunks)
    ]

    vectorstore.add_documents(
        documents=chunks,
        ids=ids,
    )

    print("Dokuman sayisi:", len(documents))
    print("Chunk sayisi:", len(chunks))
    print("ChromaDB:", CHROMA_DIR)


if __name__ == "__main__":
    ingest_documents()
