from fastapi import Header, HTTPException, status


async def get_gemini_api_key(
    x_gemini_api_key: str | None = Header(default=None),
) -> str:
    if not x_gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "MISSING_API_KEY",
                "message": (
                    "Gemini API Key tidak ditemukan. Masukkan key di panel Settings."
                ),
            },
        )
    
    # Strip whitespace and quotes from copy-paste
    clean_key = x_gemini_api_key.strip().strip("'").strip('"')
    
    if not clean_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "MISSING_API_KEY",
                "message": (
                    "Gemini API Key tidak ditemukan. Masukkan key di panel Settings."
                ),
            },
        )
    return clean_key

