import { Suspense } from "react";
import { ChatPageClient } from "@/components/chat-page-client";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ chatId: string }>;
}) {
  const { chatId } = await params;

  return (
    <main className="min-h-dvh">
      <Suspense fallback={null}>
        <ChatPageClient initialChatId={chatId} />
      </Suspense>
    </main>
  );
}
