
from langchain_chroma import Chroma

from rag.ingest import (
    CHROMA_DIR,
    COLLECTION_NAME,
    get_embeddings,
)


def search_campaign_policies(query: str) -> str:
    """Outbound operasyon ve retry politikalarinda arama yapar."""

    if not CHROMA_DIR.exists():
        return (
            "RAG indeksi bulunamadi. "
            "Once python -m rag.ingest calistirin."
        )

    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=str(CHROMA_DIR),
    )

    results = vectorstore.similarity_search(
        query=query,
        k=3,
    )

    if not results:
        return "Ilgili politika dokumani bulunamadi."

    sections = []

    for index, doc in enumerate(results, start=1):
        source = doc.metadata.get("source", "bilinmiyor")

        sections.append(
            f"[Kaynak {index}: {source}]\n"
            f"{doc.page_content}"
        )

    return "\n\n".join(sections)
