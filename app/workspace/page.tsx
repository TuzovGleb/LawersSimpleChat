import { Suspense } from "react";
import { ChatPageClient } from "@/components/chat-page-client";

export default function WorkspacePage() {
  return (
    <main className="min-h-screen">
      <Suspense fallback={null}>
        <ChatPageClient />
      </Suspense>
    </main>
  );
}
