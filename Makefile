SHELL := /bin/sh
COMPOSE_FILE := docker/app/docker-compose.yml
COMPOSE := docker compose -f $(COMPOSE_FILE)

.PHONY: help build up down restart logs logs-bot logs-listener ps config shell-bot shell-listener auth-telegram ps

help:
	@echo "AI Job Hunter — Docker commands"
	@echo "  make build           Build application image"
	@echo "  make up              Start bot and Telegram listener"
	@echo "  make down            Stop containers"
	@echo "  make restart         Restart containers"
	@echo "  make logs            Follow logs from all services"
	@echo "  make logs-bot        Follow bot logs"
	@echo "  make logs-listener   Follow listener logs"
	@echo "  make ps              Show container status"
	@echo "  make config          Validate Compose configuration"

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f --tail=100

logs-bot:
	$(COMPOSE) logs -f --tail=100 bot

logs-listener:
	$(COMPOSE) logs -f --tail=100 listener

ps:
	$(COMPOSE) ps

config:
	$(COMPOSE) config

shell-bot:
	$(COMPOSE) exec bot sh

shell-listener:
	$(COMPOSE) exec listener sh

auth-telegram:
	$(COMPOSE) run --rm --no-deps -it listener python -m scripts.auth_telegram
