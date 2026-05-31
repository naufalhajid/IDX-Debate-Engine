import asyncio
from providers.llm_factory import get_llm, llm_provider_override

async def main():
    print("Menguji Multi-Model LLM Factory...\n")

    # 1. Test Gemini (Default)
    print("--- [1/3] Menguji Provider: GEMINI ---")
    try:
        with llm_provider_override("gemini"):
            llm = get_llm("flash")
            print("Menunggu respons dari Gemini...")
            response = await llm.ainvoke("Jawab dengan singkat: Apa ibu kota Indonesia?")
            print(f"Respons Gemini: {response.content}\n")
    except Exception as e:
        print(f"Gemini gagal: {e}\n")

    # 2. Test Anthropic (Haiku)
    print("--- [2/3] Menguji Provider: ANTHROPIC ---")
    try:
        with llm_provider_override("anthropic"):
            llm = get_llm("flash")
            print("Menunggu respons dari Anthropic (Claude)...")
            response = await llm.ainvoke("Jawab dengan singkat: Apa ibu kota Jepang?")
            print(f"Respons Anthropic: {response.content}\n")
    except Exception as e:
        print(f"Anthropic gagal (Mungkin butuh login Claude Code atau set ANTHROPIC_API_KEY): {e}\n")

    # 3. Test Codex (GPT-4o-mini)
    print("--- [3/3] Menguji Provider: CODEX (OpenAI) ---")
    try:
        with llm_provider_override("codex"):
            llm = get_llm("flash")
            print("Menunggu respons dari Codex (OpenAI)...")
            response = await llm.ainvoke("Jawab dengan singkat: Apa ibu kota Prancis?")
            print(f"Respons Codex: {response.content}\n")
    except ValueError as ve:
        if "No valid Codex credentials found" in str(ve):
            print("Token Codex belum ada. Silakan login menggunakan Codex CLI terlebih dahulu dengan menjalankan perintah `codex login` di terminal Anda.")
        else:
             print(f"Codex gagal: {ve}\n")
    except Exception as e:
        print(f"Codex gagal: {e}\n")

if __name__ == "__main__":
    asyncio.run(main())
