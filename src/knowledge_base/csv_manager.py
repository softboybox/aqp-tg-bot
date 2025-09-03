import os
import errno
import csv
import json
import hashlib
import shutil
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Tuple
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import CSVLoader
from langchain.text_splitter import CharacterTextSplitter
from src.config.settings import settings

logger = logging.getLogger(__name__)

UPDATE_LOCK = asyncio.Lock()

def _is_subpath(child: str, parent: str) -> bool:
    child_p = Path(child).resolve()
    parent_p = Path(parent).resolve()
    return parent_p == child_p or parent_p in child_p.parents


def _ensure_dirs():
    directories = [
        os.path.dirname(settings.CSV_FILE_PATH),
        settings.TEMP_CSV_DIR,
        settings.BACKUP_CSV_DIR,
        settings.FAISS_INDEX_PATH,
        os.path.dirname(settings.FAISS_INDEX_TMP),
    ]
    for d in directories:
        if d:
            os.makedirs(d, exist_ok=True)
            logger.info(f"Ensured directory exists: {d}")


def _metadata_path():
    return os.path.join(settings.FAISS_INDEX_PATH, "metadata.json")


def _write_meta(meta: dict):
    os.makedirs(settings.FAISS_INDEX_PATH, exist_ok=True)
    with open(_metadata_path(), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _read_meta() -> dict:
    try:
        with open(_metadata_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read metadata: {e}")
        return {}


def _checksum(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _csv_to_texts(path: str) -> List[str]:
    for delim in [",", ";"]:
        try:
            texts = []
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                r = csv.reader(f, delimiter=delim)
                for row in r:
                    row = [c.strip() for c in row if c and c.strip()]
                    if row:
                        texts.append(" | ".join(row))
            if texts:
                logger.info(f"Successfully parsed CSV with delimiter '{delim}', {len(texts)} rows")
                return texts
        except Exception as e:
            logger.warning(f"Failed to parse CSV with delimiter '{delim}': {e}")
            continue
    raise RuntimeError("CSV не читается (кодировка/разделитель).")


def _build_faiss(texts: List[str], save_dir: str, model: str):
    logger.info(f"Building FAISS index with {len(texts)} texts using model {model}")
    emb = OpenAIEmbeddings(model=model)
    vs = FAISS.from_texts(texts=texts, embedding=emb)
    os.makedirs(save_dir, exist_ok=True)
    vs.save_local(save_dir)
    logger.info(f"FAISS index saved to {save_dir}")


def _load_vs():
    emb = OpenAIEmbeddings(model=settings.EMBEDDINGS_MODEL)
    return FAISS.load_local(
        settings.FAISS_INDEX_PATH, 
        emb, 
        allow_dangerous_deserialization=True
    )


def _clear_dir_contents(directory_path: str):
    for name in os.listdir(directory_path):
        path = os.path.join(directory_path, name)
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)
                logger.debug(f"Removed file: {path}")
            else:
                shutil.rmtree(path, ignore_errors=True)
                logger.debug(f"Removed directory: {path}")
        except Exception as ex:
            logger.warning(f"Can't remove {path}: {ex}")


async def validate_csv_file(file_path: str) -> Tuple[bool, str, dict]:
    logger.info(f"Validating CSV file: {file_path}")
    
    # Проверка размера файла
    if not os.path.exists(file_path):
        return False, "Файл не существует", {}
    
    file_size = os.path.getsize(file_path)
    max_size_bytes = settings.MAX_CSV_SIZE_MB * 1024 * 1024
    
    if file_size > max_size_bytes:
        return False, f"Файл превышает допустимый размер {settings.MAX_CSV_SIZE_MB} MB", {}
    
    try:
        texts = await asyncio.to_thread(_csv_to_texts, file_path)
        if not texts:
            return False, "CSV файл пустой", {}
        
        info = {
            "file_size": file_size,
            "row_count": len(texts),
            "file_path": file_path
        }
        
        logger.info(f"CSV validation successful: {len(texts)} rows, {file_size} bytes")
        return True, f"Валидация пройдена: {len(texts)} строк", info
        
    except Exception as e:
        logger.error(f"CSV validation failed: {e}")
        return False, f"Ошибка валидации: {str(e)}", {}


async def update_knowledge_base_atomic(temp_csv_path: str) -> Tuple[bool, str, dict]:

    _ensure_dirs()

    if _is_subpath(settings.FAISS_INDEX_TMP, settings.FAISS_INDEX_PATH):
        return False, f"FAISS_INDEX_TMP ({settings.FAISS_INDEX_TMP}) не должен находиться внутри FAISS_INDEX_PATH ({settings.FAISS_INDEX_PATH})", {}

    def _install_index_from_tmp(src_dir: str, dst_dir: str):

        os.makedirs(dst_dir, exist_ok=True)
        staging = os.path.join(dst_dir, ".staging")
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)

        for name in os.listdir(src_dir):
            s = os.path.join(src_dir, name)
            d = os.path.join(staging, name)
            if os.path.isdir(s) and not os.path.islink(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

        for name in os.listdir(staging):
            tmp_path = os.path.join(staging, name)
            final_path = os.path.join(dst_dir, name)
            if os.path.exists(final_path):
                if os.path.isdir(final_path) and not os.path.islink(final_path):
                    shutil.rmtree(final_path, ignore_errors=True)
                else:
                    try:
                        os.remove(final_path)
                    except FileNotFoundError:
                        pass
            os.replace(tmp_path, final_path)

        shutil.rmtree(staging, ignore_errors=True)
    
    async with UPDATE_LOCK:
        logger.info(f"Starting atomic knowledge base update with file: {temp_csv_path}")
        
        # 1. Валидация CSV файла
        valid, message, info = await validate_csv_file(temp_csv_path)
        if not valid:
            return False, message, info
        
        try:
            texts = await asyncio.to_thread(_csv_to_texts, temp_csv_path)
        except Exception as e:
            logger.error(f"Error reading CSV: {e}")
            return False, f"Ошибка чтения CSV: {e}", {}

        if not texts:
            return False, "CSV пустой", {}

        if os.path.exists(settings.FAISS_INDEX_TMP):
            shutil.rmtree(settings.FAISS_INDEX_TMP, ignore_errors=True)
            logger.info("Cleared old temporary index directory")
            
        try:
            await asyncio.to_thread(_build_faiss, texts, settings.FAISS_INDEX_TMP, settings.EMBEDDINGS_MODEL)
        except Exception as e:
            logger.error(f"Error building FAISS index: {e}")
            return False, f"Ошибка сборки индекса: {e}", {}

        try:
            os.makedirs(settings.FAISS_INDEX_PATH, exist_ok=True)
            
            try:
                logger.info("Installing new index via staging...")
                _install_index_from_tmp(settings.FAISS_INDEX_TMP, settings.FAISS_INDEX_PATH)
                shutil.rmtree(settings.FAISS_INDEX_TMP, ignore_errors=True)
                logger.info("Installed new index successfully")
            except Exception as e:
                logger.error(f"Error installing index: {e}")
                return False, f"Ошибка замены индекса: {e}", {}
            
        except Exception as e:
            logger.error(f"Error replacing index contents: {e}")
            return False, f"Ошибка замены индекса: {e}", {}

        try:
            vs = await asyncio.to_thread(_load_vs)
            logger.info("Successfully loaded new vector store")
        except Exception as e:
            logger.error(f"Error loading new index: {e}")
            return False, f"Индекс подменён, но не загрузился: {e}", {}

        temp_filename = os.path.basename(temp_csv_path)
        if temp_filename.startswith(tuple('0123456789')) and '_' in temp_filename:
            original_filename = temp_filename.split('_', 1)[1]
        else:
            original_filename = temp_filename
        
        new_csv_path = os.path.join(settings.CSV_DIR, original_filename)
        
        csv_backup = new_csv_path + ".bak"
        if os.path.exists(new_csv_path):
            shutil.copy2(new_csv_path, csv_backup)
            os.remove(new_csv_path)
            logger.info(f"Backed up and removed current CSV: {new_csv_path}")

        if os.path.exists(settings.CSV_FILE_PATH) and settings.CSV_FILE_PATH != new_csv_path:
            backup_default = settings.CSV_FILE_PATH + ".bak"
            shutil.copy2(settings.CSV_FILE_PATH, backup_default)
            os.remove(settings.CSV_FILE_PATH)
            logger.info(f"Backed up and removed default CSV: {settings.CSV_FILE_PATH}")

        _safe_move(temp_csv_path, new_csv_path)
        logger.info(f"Replaced CSV file (safe move): {new_csv_path}")

        if os.path.exists(csv_backup):
            os.remove(csv_backup)
        backup_default = settings.CSV_FILE_PATH + ".bak"
        if os.path.exists(backup_default):
            os.remove(backup_default)

        meta = {
            "csv_path": new_csv_path,
            "row_count": len(texts),
            "checksum": _checksum(new_csv_path),
            "built_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "csv_mtime": datetime.utcfromtimestamp(os.path.getmtime(new_csv_path)).isoformat(timespec="seconds") + "Z",
        }
        _write_meta(meta)
        logger.info("Updated metadata")

        logger.info("Knowledge base update completed successfully")
        return True, "Індекс оновлено", meta


def kb_status_meta() -> dict:
    meta = _read_meta()
    if meta.get("csv_path") and os.path.exists(meta["csv_path"]):
        try:
            meta["csv_mtime"] = datetime.utcfromtimestamp(os.path.getmtime(meta["csv_path"])).isoformat(timespec="seconds") + "Z"
        except Exception:
            pass
    return meta


def get_current_retriever():
    try:
        vs = _load_vs()
        return vs.as_retriever(
            search_type="mmr", 
            search_kwargs={'k': 10, 'lambda_mult': 0.25}
        )
    except Exception as e:
        logger.error(f"Failed to load current retriever: {e}")
        return None


def _safe_move(src: str, dst: str):
    try:
        os.replace(src, dst)
    except OSError as e:
        if e.errno == errno.EXDEV:
            shutil.copy2(src, dst)
            os.remove(src)
        else:
            raise