.PHONY: test vet build run compose-up compose-down

test:
	go test ./...

vet:
	go vet ./...

build:
	CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o bin/cloudbim-backend .

run:
	go run .

compose-up:
	docker compose up --build

compose-down:
	docker compose down
