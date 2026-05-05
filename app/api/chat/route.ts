import { NextRequest, NextResponse } from 'next/server';
import type OpenAI from 'openai';
import { v4 as uuidv4 } from 'uuid';
import { createClient } from '@/lib/supabase/server';
import { getUKLawyerPrompt } from '@/lib/prompts';
import type { ChatMessage, ChatRequestDocument, UTMData, AIResponseMetadata, SelectedModel } from '@/lib/types';
import { generateAIResponse } from '@/lib/ai-service';

// Ленивая инициализация OpenAI - только при наличии API ключа
// Используем динамический импорт, чтобы избежать выполнения кода во время сборки
async function getOpenAIClient(): Promise<InstanceType<typeof import('openai').default> | null> {
  if (!process.env.OPENAI_API_KEY) {
    return null;
  }
  const OpenAIModule = await import('openai');
  const OpenAI = OpenAIModule.default;
  return new OpenAI({
    apiKey: process.env.OPENAI_API_KEY!,
  });
}

// Отключаем статическую генерацию для этого route
export const dynamic = 'force-dynamic';
// Используем Node.js runtime для Yandex Cloud Serverless Containers
// Edge Runtime имеет ограничения по DNS lookup в Yandex Cloud
export const runtime = 'nodejs';

export async function POST(req: NextRequest) {
  try {
    const {
      messages,
      sessionId,
      userId,
      projectId,
      selectedModel,
    }: {
      messages: ChatMessage[];
      sessionId?: string;
      userId?: string;
      projectId?: string;
      selectedModel?: SelectedModel;
    } = await req.json();

    if (!messages || messages.length === 0) {
      return NextResponse.json({ error: 'Messages are required' }, { status: 400 });
    }

    // Extract UTM parameters
    const url = new URL(req.url);
    const utm: UTMData = {};
    url.searchParams.forEach((value, key) => {
      if (key.startsWith('utm_')) {
        utm[key as keyof UTMData] = value;
      }
    });

    const lawyerPrompt = getUKLawyerPrompt();

    const supabase = await createClient();
    let resolvedProjectId = projectId;
    if (!resolvedProjectId && sessionId) {
      try {
        const { data: sessionRow } = await supabase
          .from('chat_sessions')
          .select('project_id')
          .eq('id', sessionId)
          .maybeSingle();
        if (sessionRow?.project_id) {
          resolvedProjectId = sessionRow.project_id;
        }
      } catch (error) {
        console.error('Failed to resolve project for session:', error);
      }
    }

    const documentsById = await loadAttachedDocumentsById(supabase, resolvedProjectId, messages);

    // Format messages for OpenAI
    // Используем тип из OpenAI, но не создаем экземпляр клиента до проверки
    type ChatMessageParam = OpenAI.Chat.Completions.ChatCompletionMessageParam;
    const formattedMessages: ChatMessageParam[] = [
      { role: "system", content: lawyerPrompt },
      ...messages.map(msg => ({
        role: msg.role as "user" | "assistant",
        content: formatMessageContentWithAttachments(msg, documentsById),
      }))
    ];

    console.log('[AI] Full messages sent to AI:\n', JSON.stringify(
      formattedMessages.map((msg) => ({
        role: msg.role,
        content: typeof msg.content === 'string'
          ? msg.content.slice(0, 2000) + (msg.content.length > 2000 ? `\n…[+${msg.content.length - 2000} chars]` : '')
          : msg.content,
      })),
      null,
      2,
    ));

    // Получаем последнее сообщение пользователя для анализа
    const lastUserMessage = messages[messages.length - 1]?.content || '';

    // --- AI Service call with automatic fallback and chunking ---
    // Используем streaming для отправки heartbeat во время обработки
    // Это предотвращает закрытие соединения балансировщиком Yandex Cloud
    const openaiClient = await getOpenAIClient();
    if (!openaiClient) {
      return NextResponse.json(
        { error: 'OpenAI API key is not configured' },
        { status: 500 }
      );
    }

    // Создаем ReadableStream для отправки heartbeat
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        let heartbeatInterval: NodeJS.Timeout | null = null;
        
        try {
          // Отправляем начальный heartbeat сразу
          controller.enqueue(encoder.encode(': heartbeat\n\n'));
          
          // Отправляем heartbeat каждые 5 секунд для поддержания соединения
          heartbeatInterval = setInterval(() => {
            try {
              controller.enqueue(encoder.encode(': heartbeat\n\n'));
            } catch (e) {
              // Соединение закрыто, останавливаем heartbeat
              if (heartbeatInterval) {
                clearInterval(heartbeatInterval);
              }
            }
          }, 5000);

          // Выполняем генерацию AI ответа
          const aiResponse = await generateAIResponse(
            openaiClient,
            formattedMessages,
            lastUserMessage,
            undefined, // forceModel - используем автоматический выбор
            selectedModel // selectedModel для OpenRouter
          );

          // Останавливаем heartbeat
          if (heartbeatInterval) {
            clearInterval(heartbeatInterval);
            heartbeatInterval = null;
          }

          const assistantMessage = aiResponse.content;
          
          // Логирование метаданных ответа
          const metadata: AIResponseMetadata = {
            modelUsed: aiResponse.modelUsed,
            fallbackOccurred: aiResponse.fallbackOccurred,
            fallbackReason: aiResponse.fallbackReason,
            chunksCount: aiResponse.chunksCount,
            totalTokens: aiResponse.totalTokens,
            finishReason: aiResponse.finishReason,
            responseTimeMs: aiResponse.responseTimeMs,
            provider: aiResponse.provider, // Добавляем информацию о провайдере
          };
          
          console.log('AI Response metadata:', metadata);
          
          // Предупреждение если ответ был обрезан даже после chunking
          if (aiResponse.finishReason === 'length') {
            console.warn('Response was truncated even after chunking. Consider reviewing chunking limits.');
          }
          
          // Ensure we have a valid response
          if (!assistantMessage || assistantMessage.trim() === '') {
            console.error('AI Service returned empty response', metadata);
            throw new Error('Empty response from AI Service');
          }

          let currentSessionId = sessionId;

          // Create new session only if it doesn't exist
          if (!currentSessionId) {
            const newSessionId = uuidv4();
            try {
              const newChatSession = {
                id: newSessionId,
                user_id: userId ?? null,
                project_id: resolvedProjectId ?? null,
                initial_message: messages[0].content,
                created_at: new Date().toISOString(),
                utm: utm || null,
              };
              const { error: sessionError } = await supabase
                .from('chat_sessions')
                .insert([
                  {
                    id: newChatSession.id,
                    user_id: newChatSession.user_id,
                    project_id: newChatSession.project_id,
                    initial_message: newChatSession.initial_message,
                    created_at: newChatSession.created_at,
                    utm: newChatSession.utm,
                  },
                ]);
              if (sessionError) {
                console.error('Error creating session:', sessionError);
              } else {
                currentSessionId = newSessionId;
              }
            } catch (error) {
              console.error('Error with Supabase session creation:', error);
            }
          }

          // Save messages to database
          try {
            if (currentSessionId) {
              const messageRows = [
                {
                  session_id: currentSessionId,
                  role: 'user',
                  content: messages[messages.length - 1].content,
                  attached_document_ids: getAttachedDocumentIds(messages[messages.length - 1]),
                  created_at: new Date().toISOString(),
                },
                {
                  session_id: currentSessionId,
                  role: 'assistant',
                  content: assistantMessage,
                  attached_document_ids: [],
                  created_at: new Date().toISOString(),
                },
              ];
              const { error: messageError } = await supabase
                .from('chat_messages')
                .insert(messageRows);

              if (messageError) {
                console.error('Error saving messages:', messageError);
              }
            }
          } catch (error) {
            console.error('Error with Supabase message saving:', error);
          }

          // Отправляем финальный ответ через SSE
          const response = {
            message: assistantMessage,
            sessionId: currentSessionId,
            projectId: resolvedProjectId,
            metadata,
          };
          
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(response)}\n\n`));
          controller.close();
          
        } catch (error) {
          // Останавливаем heartbeat при ошибке
          if (heartbeatInterval) {
            clearInterval(heartbeatInterval);
          }
          
          console.error('Error in chat API (streaming):', error);
          
          const errorMessage = error instanceof Error ? error.message : 'Unknown error';
          const errorResponse = {
            error: 'Internal server error',
            details: errorMessage,
          };
          
          try {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(errorResponse)}\n\n`));
            controller.close();
          } catch (closeError) {
            // Игнорируем ошибки при закрытии
          }
        }
      },
    });

    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no', // Отключаем буферизацию nginx/балансировщика
      },
    });

  } catch (error) {
    console.error('Error in chat API:', error);
    
    // Provide more specific error information for debugging
    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
    console.error('Error details:', {
      message: errorMessage,
      stack: error instanceof Error ? error.stack : undefined,
    });
    
    return NextResponse.json(
      { error: 'Internal server error', details: errorMessage },
      { status: 500 }
    );
  }
}

const MAX_CONTEXT_DOCUMENTS = 20;
const MAX_CHARACTERS_PER_DOCUMENT = 50000;

function getAttachedDocumentIds(message: ChatMessage | undefined) {
  if (!message || !Array.isArray(message.attachedDocumentIds)) {
    return [];
  }

  return Array.from(
    new Set(message.attachedDocumentIds.filter((id) => typeof id === 'string' && id.trim())),
  ).slice(0, MAX_CONTEXT_DOCUMENTS);
}

async function loadAttachedDocumentsById(
  supabase: Awaited<ReturnType<typeof createClient>>,
  projectId: string | undefined,
  messages: ChatMessage[],
): Promise<Map<string, ChatRequestDocument>> {
  const ids = Array.from(
    new Set(messages.flatMap((message) => getAttachedDocumentIds(message))),
  ).slice(0, MAX_CONTEXT_DOCUMENTS);

  if (!projectId || ids.length === 0) {
    return new Map();
  }

  try {
    const { data, error } = await supabase
      .from('project_documents')
      .select('id, name, text')
      .eq('project_id', projectId)
      .in('id', ids);

    if (error) {
      console.error('Failed to load attached documents for context:', error);
      return new Map();
    }

    return new Map(
      (data ?? [])
        .filter((doc: any) => typeof doc?.id === 'string' && typeof doc?.text === 'string' && doc.text.trim())
        .map((doc: any) => [
          doc.id,
          {
            id: doc.id,
            name: typeof doc.name === 'string' && doc.name.trim() ? doc.name : 'Документ',
            text: doc.text,
          },
        ]),
    );
  } catch (error) {
    console.error('Unexpected error while loading attached documents:', error);
    return new Map();
  }
}

function formatMessageContentWithAttachments(
  message: ChatMessage,
  documentsById: Map<string, ChatRequestDocument>,
) {
  const attachmentIds = getAttachedDocumentIds(message);
  if (message.role !== 'user' || attachmentIds.length === 0) {
    return message.content;
  }

  const prepared = attachmentIds
    .map((id) => documentsById.get(id))
    .filter((doc): doc is ChatRequestDocument => Boolean(doc))
    .map((doc) => {
      const text = doc.text.length > MAX_CHARACTERS_PER_DOCUMENT
        ? `${doc.text.slice(0, MAX_CHARACTERS_PER_DOCUMENT)}\n\n[Текст документа усечён]`
        : doc.text;

      return `Документ: ${doc.name}\n\n${text}`;
    });

  if (prepared.length === 0) {
    return message.content;
  }

  return `${message.content}\n\n[Прикрепленные документы к этому сообщению]\n\n${prepared.join('\n\n---\n\n')}`;
}
