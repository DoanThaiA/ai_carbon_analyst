import logging
from typing import List, Optional
import cohere
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from services.embedding import Embedder
from schemas.retrieval_models import RetrievedDocument

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self, embedder: Embedder, session: AsyncSession):
        self.embedder = embedder
        self.session = session
        
        self.settings = Settings.from_env()
        self.cohere_client = cohere.AsyncClient(api_key=self.settings.cohere_api_key)
        self.rerank_model = self.settings.rerank_model

    async def _hybrid_search(
        self,
        query: str,
        query_embedding: List[float],
        limit: int = 20,
        rrf_k: int = 60,
        report_date: Optional[str] = None,
        only_source_type: Optional[str] = None,
    ) -> List[RetrievedDocument]:
        """Thực hiện Hybrid Search bằng SQL CTE và tính điểm RRF.

        `only_source_type` (vd "article"): giới hạn kết quả về đúng 1 loại
        `chunks.source_type`, loại hẳn loại còn lại ra khỏi ứng viên NGAY TỪ
        vòng hybrid search (trước rerank) — dùng cho Quote Chat vì đoạn quote
        người dùng bôi đen vốn trích ĐÚNG NGUYÊN VĂN từ chunk của chính báo cáo
        (`source_type='report'`), nên chunk đó gần như luôn khớp tuyệt đối và
        chiếm hết top-k, đẩy chunk bài báo thật (`source_type='article'`, có
        URL để trích nguồn) ra ngoài — trong khi nội dung quote đã có sẵn
        nguyên văn trong system prompt nên retrieve lại chunk report là dư
        thừa.
        """

        # Nếu có report_date, chặn 2 đầu để lấy đúng tin tức đã crawl cho báo cáo ngày đó
        filter_cte = ""
        conditions: List[str] = []
        params = {
            "query_embedding": f"[{','.join(str(f) for f in query_embedding)}]",
            "query_text": query,
            "limit": limit,
            "rrf_k": rrf_k,
        }

        if report_date:
            from datetime import datetime, timedelta
            target_date = datetime.strptime(report_date, "%Y-%m-%d")
            # PHẢI khớp đúng khung ngày dùng ở report_generator.py::get_news_for_report —
            # đó là nguồn xác thực duy nhất cho "báo cáo ngày T dùng tin nào". Báo cáo
            # ngày T lấy tin crawl trong [T 00:00 UTC, (T+1) 00:00 UTC) — tức 07:00 (VN)
            # ngày T -> 07:00 (VN) ngày T+1 (DB lưu UTC, VN = UTC+7 nên 07:00 VN ngày T
            # == 00:00 UTC ngày T). TRƯỚC ĐÂY code này lệch +1 ngày ([T+1, T+2)) khiến
            # Quote Chat gần như luôn lọc sai khung ngày, tìm không ra bài báo thật của
            # đúng report_date -> "Danh sách tin tức tham khảo" rỗng dù dữ liệu đã có sẵn.
            start_date = target_date
            end_date = target_date + timedelta(days=1)

            filter_cte = """
            article_filter AS (
                SELECT id FROM articles WHERE crawled_at >= :start_date AND crawled_at < :end_date
            ),
            report_filter AS (
                SELECT id FROM reports WHERE report_date = :report_date
            ),
            """
            conditions.append(
                "((source_type = 'article' AND source_id IN (SELECT id FROM article_filter))"
                " OR (source_type = 'report' AND source_id IN (SELECT id FROM report_filter)))"
            )
            params["start_date"] = start_date
            params["end_date"] = end_date
            params["report_date"] = report_date

        if only_source_type:
            conditions.append("source_type = :only_source_type")
            params["only_source_type"] = only_source_type

        vector_where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        fts_extra_where = (" AND " + " AND ".join(conditions)) if conditions else ""

        sql = text(f'''
            WITH {filter_cte}
            vector_search AS (
                SELECT chunk_id, source_type, source_id, content,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> :query_embedding) as rank
                FROM chunks
                {vector_where}
                ORDER BY embedding <=> :query_embedding
                LIMIT :limit
            ),
            fts_search AS (
                SELECT chunk_id, source_type, source_id, content,
                       ROW_NUMBER() OVER (ORDER BY ts_rank_cd(content_tsv, query) DESC) as rank
                FROM chunks, websearch_to_tsquery('english', :query_text) query
                WHERE content_tsv @@ query{fts_extra_where}
                ORDER BY ts_rank_cd(content_tsv, query) DESC
                LIMIT :limit
            ),
            combined AS (
                SELECT
                    COALESCE(v.chunk_id, f.chunk_id) as chunk_id,
                    COALESCE(v.source_type, f.source_type) as source_type,
                    COALESCE(v.source_id, f.source_id) as source_id,
                    COALESCE(v.content, f.content) as content,
                    COALESCE(1.0 / (:rrf_k + v.rank), 0.0) + COALESCE(1.0 / (:rrf_k + f.rank), 0.0) as rrf_score
                FROM vector_search v
                FULL OUTER JOIN fts_search f ON v.chunk_id = f.chunk_id
            )
            SELECT
                c.chunk_id, c.source_type, c.source_id, c.content, c.rrf_score,
                a.source as source_name,
                a.published_at as published_at,
                a.url as url,
                a.title as title
            FROM combined c
            LEFT JOIN articles a ON c.source_type = 'article' AND a.id = c.source_id
            ORDER BY c.rrf_score DESC
            LIMIT :limit;
        ''')

        result = await self.session.execute(sql, params)

        docs = []
        for row in result.fetchall():
            docs.append(
                RetrievedDocument(
                    chunk_id=row.chunk_id,
                    source_type=row.source_type,
                    source_id=row.source_id,
                    content=row.content,
                    score=float(row.rrf_score),
                    source_name=row.source_name,
                    published_at=row.published_at,
                    url=row.url,
                    title=row.title,
                )
            )
        return docs

    async def _rerank(
        self, query: str, documents: List[RetrievedDocument], top_k: int
    ) -> List[RetrievedDocument]:
        """Sử dụng Cohere API để rerank danh sách documents."""
        if not documents:
            return []

        docs_text = [doc.content for doc in documents]
        
        response = await self.cohere_client.rerank(
            model=self.rerank_model,
            query=query,
            documents=docs_text,
            top_n=top_k,
            return_documents=False
        )

        reranked_docs = []
        for result in response.results:
            idx = result.index
            original_doc = documents[idx]
            reranked_docs.append(
                RetrievedDocument(
                    chunk_id=original_doc.chunk_id,
                    source_type=original_doc.source_type,
                    source_id=original_doc.source_id,
                    content=original_doc.content,
                    score=result.relevance_score,
                    source_name=original_doc.source_name,
                    published_at=original_doc.published_at,
                    url=original_doc.url,
                    title=original_doc.title,
                )
            )
            
        return reranked_docs

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        hybrid_limit: int = 20,
        rrf_k: int = 60,
        report_date: Optional[str] = None,
        only_source_type: Optional[str] = None,
    ) -> List[RetrievedDocument]:
        """
        Thực hiện toàn bộ quá trình retrieval:
        1. Embed query
        2. Hybrid Search (Vector + FTS) với thuật toán RRF
        3. Rerank bằng Cohere
        """
        logger.info("[RETRIEVAL] Đang embed query...")
        # input_type="search_query" — bất đối xứng với "search_document" dùng lúc
        # index chunk, đúng khuyến nghị của Cohere cho embed model v3+/v4.
        query_embeddings = await self.embedder.embed([query], input_type="search_query")
        if not query_embeddings:
            logger.warning("Không thể sinh embedding cho query.")
            return []
        query_embedding = query_embeddings[0]

        logger.info("[RETRIEVAL] Đang thực hiện Hybrid Search (RRF)...")
        hybrid_results = await self._hybrid_search(
            query=query,
            query_embedding=query_embedding,
            limit=hybrid_limit,
            rrf_k=rrf_k,
            report_date=report_date,
            only_source_type=only_source_type,
        )
        logger.info("[RETRIEVAL] Hybrid Search tìm thấy %d chunks.", len(hybrid_results))

        if not hybrid_results:
            return []

        logger.info("[RETRIEVAL] Đang thực hiện Reranking với Cohere...")
        final_results = await self._rerank(
            query=query, documents=hybrid_results, top_k=top_k
        )
        logger.info("[RETRIEVAL] Rerank hoàn tất, giữ lại top %d chunks.", len(final_results))

        return final_results
