# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
COPY pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn -q -B dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -q -B -DskipTests package

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
COPY --from=build /build/target/spring-ai-fastpath-*.jar app.jar
EXPOSE 8080
# CDS + tiered compilation flags trim startup and warm-up latency.
ENTRYPOINT ["java", "-XX:+UseZGC", "-XX:TieredStopAtLevel=1", "-Xshare:auto", "-jar", "app.jar"]
