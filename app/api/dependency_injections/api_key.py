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
                    "Gemini API Key not found. Please enter your key in the Settings panel."
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
                    "Gemini API Key not found. Please enter your key in the Settings panel."
                ),
            },
        )
    return clean_key

