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
    if not x_gemini_api_key.startswith("AIza"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_API_KEY_FORMAT",
                "message": (
                    "Format API Key tidak valid. Key harus diawali dengan 'AIza'."
                ),
            },
        )
    return x_gemini_api_key
