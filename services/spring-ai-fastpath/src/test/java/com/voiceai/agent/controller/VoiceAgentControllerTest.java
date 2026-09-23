package com.voiceai.agent.controller;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.voiceai.agent.model.ChatMessage;
import com.voiceai.agent.model.SlowPathEvent;
import com.voiceai.agent.service.SlowPathDispatcher;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.messages.MessageType;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.model.Generation;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.reactive.WebFluxTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

@WebFluxTest(controllers = VoiceAgentController.class)
@Import({com.voiceai.agent.config.AgentConfig.class, VoiceAgentControllerTest.Stubs.class})
@TestPropertySource(properties = {
        "spring.ai.openai.chat.options.model=stub-model",
        "voiceagent.system-prompt=test prompt",
        "voiceagent.n8n.webhook-url=http://n8n.test/webhook/call-event"
})
class VoiceAgentControllerTest {

    static final List<String> TOKENS = List.of("Sure, ", "I ", "can ", "help ", "with ", "that.");
    static final Duration N8N_DELAY = Duration.ofMillis(300);

    @TestConfiguration
    static class Stubs {

        @Bean
        @Primary
        ChatModel stubChatModel() {
            return new ChatModel() {
                @Override
                public ChatResponse call(Prompt prompt) {
                    return new ChatResponse(List.of(new Generation(new AssistantMessage(String.join("", TOKENS)))));
                }

                @Override
                public Flux<ChatResponse> stream(Prompt prompt) {
                    return Flux.fromIterable(TOKENS)
                            .map(t -> new ChatResponse(List.of(new Generation(new AssistantMessage(t)))));
                }
            };
        }

        @Bean
        @Primary
        RecordingDispatcher recordingDispatcher(WebClient n8nWebClient) {
            return new RecordingDispatcher(n8nWebClient);
        }
    }

    /** Records dispatched events and simulates a slow n8n by sleeping before recording. */
    static class RecordingDispatcher extends SlowPathDispatcher {
        final List<SlowPathEvent> events = new CopyOnWriteArrayList<>();
        final CountDownLatch delivered = new CountDownLatch(1);

        RecordingDispatcher(WebClient n8n) {
            super(n8n, 5000);
        }

        @Override
        public void dispatch(SlowPathEvent event) {
            Thread.ofVirtual().start(() -> {
                try {
                    Thread.sleep(N8N_DELAY);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                }
                events.add(event);
                delivered.countDown();
            });
        }
    }

    @Autowired WebTestClient client;
    @Autowired RecordingDispatcher dispatcher;
    @Autowired ObjectMapper mapper;

    @Test
    void streamsOpenAiFormattedSseAndDoesNotBlockOnWebhook() throws Exception {
        String body = """
                {"model":"x","stream":true,
                 "messages":[{"role":"system","content":"s"},{"role":"user","content":"hi"}],
                 "call":{"id":"call_42","customer":{"number":"+15550001111"}}}
                """;

        long start = System.nanoTime();
        List<String> frames = client.post()
                .uri("/v1/chat/completions")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(body)
                .exchange()
                .expectStatus().isOk()
                .expectHeader().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM)
                .returnResult(String.class)
                .getResponseBody()
                .collectList()
                .block(Duration.ofSeconds(5));
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;

        assertThat(frames).isNotNull();
        assertThat(frames.get(frames.size() - 1)).isEqualTo("[DONE]");
        assertThat(elapsedMs).as("stream must not wait for the webhook").isLessThan(N8N_DELAY.toMillis());
        assertThat(dispatcher.events).as("webhook not yet delivered when response completed").isEmpty();

        List<JsonNode> chunks = frames.subList(0, frames.size() - 1).stream().map(this::json).toList();
        assertThat(chunks).allSatisfy(c -> assertThat(c.get("object").asText()).isEqualTo("chat.completion.chunk"));
        assertThat(chunks.get(0).at("/choices/0/delta/role").asText()).isEqualTo("assistant");
        assertThat(chunks.get(chunks.size() - 1).at("/choices/0/finish_reason").asText()).isEqualTo("stop");

        StringBuilder content = new StringBuilder();
        chunks.forEach(c -> content.append(c.at("/choices/0/delta/content").asText("")));
        assertThat(content.toString()).isEqualTo(String.join("", TOKENS));
        assertThat(chunks.size()).isEqualTo(TOKENS.size() + 2);

        assertThat(dispatcher.delivered.await(2, TimeUnit.SECONDS)).isTrue();
        SlowPathEvent evt = dispatcher.events.get(0);
        assertThat(evt.event_type()).isEqualTo("turn.completed");
        assertThat(evt.call_id()).isEqualTo("call_42");
        assertThat(evt.phone_number()).isEqualTo("+15550001111");
        assertThat(evt.user_text()).isEqualTo("hi");
        assertThat(evt.assistant_text()).isEqualTo(String.join("", TOKENS));
        assertThat(evt.latency_ms()).containsKeys("ttft", "total");
    }

    @Test
    void parsesContentPartsAndMapsRoles() throws Exception {
        ChatMessage parts = mapper.readValue(
                "{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"hel\"},{\"type\":\"text\",\"text\":\"lo\"}]}",
                ChatMessage.class);
        assertThat(parts.text()).isEqualTo("hello");

        var msgs = VoiceAgentController.toSpringMessages(List.of(
                new ChatMessage("system", mapper.readTree("\"s\"")),
                new ChatMessage("user", mapper.readTree("\"u\"")),
                new ChatMessage("assistant", mapper.readTree("\"a\"")),
                new ChatMessage("tool", mapper.readTree("\"ignored\""))));
        assertThat(msgs).extracting(m -> m.getMessageType())
                .containsExactly(MessageType.SYSTEM, MessageType.USER, MessageType.ASSISTANT);
    }

    private JsonNode json(String s) {
        try {
            return mapper.readTree(s);
        } catch (Exception e) {
            throw new AssertionError("invalid JSON frame: " + s, e);
        }
    }
}
