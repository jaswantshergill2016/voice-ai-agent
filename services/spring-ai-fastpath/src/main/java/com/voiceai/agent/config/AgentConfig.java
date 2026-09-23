package com.voiceai.agent.config;

import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
public class AgentConfig {

    @Bean
    ChatClient chatClient(ChatModel chatModel, @Value("${voiceagent.system-prompt}") String systemPrompt) {
        return ChatClient.builder(chatModel).defaultSystem(systemPrompt).build();
    }

    @Bean
    WebClient n8nWebClient(@Value("${voiceagent.n8n.webhook-url}") String webhookUrl) {
        return WebClient.builder().baseUrl(webhookUrl).build();
    }
}
