package com.voiceai.agent.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.JsonNode;

/** Message with either string content or OpenAI content-part array. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ChatMessage(String role, JsonNode content) {

    public String text() {
        if (content == null || content.isNull()) {
            return "";
        }
        if (content.isTextual()) {
            return content.asText();
        }
        if (content.isArray()) {
            StringBuilder sb = new StringBuilder();
            content.forEach(part -> {
                JsonNode t = part.get("text");
                if (t != null && t.isTextual()) {
                    sb.append(t.asText());
                }
            });
            return sb.toString();
        }
        return content.toString();
    }
}
