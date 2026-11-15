# td-openai-httpx2

OpenAI SDK 3.x работает на httpx2, а Hub передаёт httpx.AsyncClient через cast(Any); нужен явный клиент SDK и моки тестов без respx-на-httpx.
