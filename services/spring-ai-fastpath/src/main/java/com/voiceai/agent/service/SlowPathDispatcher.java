package com.voiceai.agent.service;

import com.voiceai.agent.model.SlowPathEvent;
import java.time.Duration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

/** Fire-and-forget POST to n8n. Subscribes on a separate scheduler; never joins the SSE stream. */
@Service
public class SlowPathDispatcher {

    private static final Logger log = LoggerFactory.getLogger(SlowPathDispatcher.class);

    private final WebClient n8n;
    private final Duration timeout;

    public SlowPathDispatcher(WebClient n8nWebClient, @Value("${voiceagent.n8n.timeout-ms:5000}") long timeoutMs) {
        this.n8n = n8nWebClient;
        this.timeout = Duration.ofMillis(timeoutMs);
    }

    public void dispatch(SlowPathEvent event) {
        n8n.post()
                .bodyValue(event)
                .retrieve()
                .toBodilessEntity()
                .timeout(timeout)
                .doOnSuccess(r -> log.info("n8n {} call={} status={}", event.event_type(), event.call_id(), r.getStatusCode()))
                .doOnError(e -> log.warn("n8n dispatch failed call={}: {}", event.call_id(), e.toString()))
                .onErrorResume(e -> Mono.empty())
                .subscribeOn(Schedulers.boundedElastic())
                .subscribe();
    }
}
