package com.voiceai.agent.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.util.List;
import java.util.Map;

/** Subset of the OpenAI chat completion request that Vapi's custom-llm sends. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ChatCompletionRequest(
        String model,
        List<ChatMessage> messages,
        Boolean stream,
        Map<String, Object> call,
        Map<String, Object> metadata) {

    public ChatCompletionRequest {
        messages = messages == null ? List.of() : messages;
        metadata = metadata == null ? Map.of() : metadata;
    }

    public String callId() {
        if (call != null && call.get("id") != null) {
            return String.valueOf(call.get("id"));
        }
        Object metaId = metadata.get("call_id");
        return metaId == null ? null : String.valueOf(metaId);
    }

    @SuppressWarnings("unchecked")
    public String phoneNumber() {
        if (call == null || !(call.get("customer") instanceof Map<?, ?> customer)) {
            return null;
        }
        Object number = ((Map<String, Object>) customer).get("number");
        return number == null ? null : String.valueOf(number);
    }

    public String lastUserText() {
        for (int i = messages.size() - 1; i >= 0; i--) {
            if ("user".equals(messages.get(i).role())) {
                return messages.get(i).text();
            }
        }
        return "";
    }
}
