"""Text chunker.

Split documents into chunks of ~500 tokens with ~50 token overlap.
Attach metadata: doc_id, filename, section, chunk_index, token_count, char_offset.
"""
