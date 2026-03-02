# Request-to-Data Routing Flow

This document captures the end-to-end runtime flow from client request to
provider data fetch in the current stock-mcp architecture.

## Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    participant Client as Client(REST/MCP)
    participant API as API/Tool Layer
    participant UC as UseCase
    participant GW as MarketGateway
    participant SR as SymbolResolver
    participant SM as SecurityMaster(DB)
    participant RT as MarketRouter
    participant HP as HealthTracker
    participant AM as AdapterManager(Fallback)
    participant AD as Provider Adapter

    Client->>API: query(symbol, op)
    API->>UC: call use_case
    UC->>GW: get_real_time_price / get_historical_prices

    GW->>SR: resolve(raw_symbol)
    SR->>SR: rule chain normalize -> EXCHANGE:SYMBOL
    SR->>SM: _persist_resolution(find/upsert listing, alias, canonical_id)
    SM-->>SR: asset_id + metadata
    SR-->>GW: InstrumentRef(normalized, asset_type, exchange, canonical_id)

    GW->>RT: route by instrument + data_type

    RT->>RT: select providers by policy(asset_type, exchange, data_type)
    RT->>SM: get_provider_symbols(asset_id, data_type)
    SM-->>RT: provider_symbol map

    loop provider in priority order
        RT->>HP: is_available(provider)?
        alt available
            RT->>AD: fetch(by provider_symbol or normalized)
            alt success
                RT->>HP: record(success, latency)
                AD-->>RT: market data
                RT-->>GW: return data
            else empty/error
                RT->>HP: record(empty/error, latency)
            end
        else cooldown
            RT->>RT: skip provider
        end
    end

    alt all providers failed
        RT->>AM: legacy fallback dispatch
        AM->>AD: try primary + fallbacks
        AD-->>AM: data or none
        AM-->>RT: fallback result
    end

    RT-->>GW: final result
    GW-->>UC: normalized response / symbol error
    UC-->>API: DTO/JSON
    API-->>Client: response
```

## Key Notes

- Symbol normalization and persistence happen before provider routing.
- Routing decision key is `(asset_type, exchange, data_type)`.
- Health tracker can temporarily cooldown unstable providers.
- Legacy adapter fallback is the final resilience layer.
