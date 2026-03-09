import { ChatOllama } from "@langchain/ollama";

async function main() {
    const chat = new ChatOllama({ model: "llama3.2" });
    const res = await chat.invoke([{role: "user", content: "hi"}], { temperature: 0.1 });
    console.log(res);
}
main().catch(console.error);
