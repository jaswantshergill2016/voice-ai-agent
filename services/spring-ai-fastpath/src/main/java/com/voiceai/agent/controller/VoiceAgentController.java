package com.voiceai.agent.controller;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.voiceai.agent.model.ChatCompletionRequest;
import com.voiceai.agent.model.ChatMessage;
import com.voiceai.agent.model.SlowPathEvent;
import com.voiceai.agent.service.SlowPathDispatcher;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.messages.Message;
import org.springframework.ai.chat.messages.SystemMessage;
import org.springframework.ai.chat.messages.UserMessage;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;

/**
 * OpenAI-compatible SSE endpoint consumed by Vapi's custom-llm provider.
 *
 * <p>Each emitted element is one {@code data:} frame. WebFlux adds the SSE framing
 * for {@code text/event-stream}; we emit the JSON body of each chunk.
 */
@RestController
public class VoiceAgentController {

    private static final Logger log = LoggerFactory.getLogger(VoiceAgentController.class);
    private static final String DONE = "[DONE]";

    private final ChatClient chatClient;
    private final SlowPathDispatcher slowPath;
    private final ObjectMapper mapper;
    private final String modelName;

    public VoiceAgentController(
            ChatClient chatClient,
            SlowPathDispatcher slowPath,
            ObjectMapper mapper,
            @Value("${spring.ai.openai.chat.options.model}") String modelName) {
        this.chatClient = chatClient;
        this.slowPath = slowPath;
        this.mapper = mapper;
        this.modelName = modelName;
    }

    @PostMapping(value = "/v1/chat/completions", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> chatCompletions(@RequestBody ChatCompletionRequest request) {
        final long start = System.nanoTime();
        final String completionId = "chatcmpl-" + UUID.randomUUID().toString().replace("-", "").substring(0, 24);
        final long created = Instant.now().getEpochSecond();
        final String callId = request.callId() != null ? request.callId() : "local-" + UUID.randomUUID();
        final AtomicLong firstTokenNanos = new AtomicLong(-1);
        final StringBuilder transcript = new StringBuilder();

        Flux<String> tokens = chatClient
                .prompt()
                .messages(toSpringMessages(request.messages()))
                .stream()
                .content()
                .filter(t -> !t.isEmpty())
                .doOnNext(t -> {
                    firstTokenNanos.compareAndSet(-1, System.nanoTime());
                    transcript.append(t);
                })
                .map(t -> chunk(completionId, created, Map.of("content", t), null));

        Flux<String> head = Flux.just(chunk(completionId, created, Map.of("role", "assistant", "content", ""), null));
        Flux<String> tail = Flux.just(chunk(completionId, created, Map.of(), "stop"), DONE);

        return Flux.concat(head, tokens, tail)
                .doOnComplete(() -> {
                    double totalMs = (System.nanoTime() - start) / 1_000_000.0;
                    double ttftMs = firstTokenNanos.get() < 0 ? 0 : (firstTokenNanos.get() - start) / 1_000_000.0;
                    log.info("call={} ttft_ms={} total_ms={}", callId, fmt(ttftMs), fmt(totalMs));
                    slowPath.dispatch(new SlowPathEvent(
                            "turn.completed",
                            callId,
                            request.phoneNumber(),
                            request.lastUserText(),
                            transcript.toString(),
                            Map.of("ttft", round1(ttftMs), "total", round1(totalMs)),
                            request.metadata()));
                });
    }

    @GetMapping("/healthz")
    public Map<String, String> healthz() {
        return Map.of("status", "ok", "model", modelName);
    }

    static List<Message> toSpringMessages(List<ChatMessage> messages) {
        List<Message> out = new ArrayList<>(messages.size());
        for (ChatMessage m : messages) {
            String text = m.text();
            switch (m.role()) {
                case "system" -> out.add(new SystemMessage(text));
                case "assistant" -> out.add(new AssistantMessage(text));
                case "user" -> out.add(new UserMessage(text));
                default -> { /* tool messages are not forwarded */ }
            }
        }
        return out;
    }

    String chunk(String id, long created, Map<String, String> delta, String finishReason) {
        Map<String, Object> choice = new LinkedHashMap<>();
        choice.put("index", 0);
        choice.put("delta", delta);
        choice.put("finish_reason", finishReason);

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("id", id);
        body.put("object", "chat.completion.chunk");
        body.put("created", created);
        body.put("model", modelName);
        body.put("choices", List.of(choice));
        try {
            return mapper.writeValueAsString(body);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private static double round1(double v) {
        return Math.round(v * 10.0) / 10.0;
    }

    private static String fmt(double v) {
        return String.format("%.1f", v);
    }
}
