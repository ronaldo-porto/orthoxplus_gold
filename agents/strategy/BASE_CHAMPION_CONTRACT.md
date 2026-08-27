# BaseStrategy Champion Contract — V4.13.8

Promotion chain: `Strategy1_Research V4.13.8 -> BaseStrategy -> AdaptiveAgent`.

## Identity

- `DEPLOY_POLICY_VERSION = "base_v4_13_8_champion"`
- `BASE_CHAMPION = True`
- `BASE_CHAMPION_FROZEN = True`
- parent policy: `simplified_kappa_productivity_v4_13_8`

## Promoted verified behavior

BaseStrategy consumes the exact frozen V4.13.8 Research engine and therefore keeps:

- V4.13.4 authoritative lane propagation;
- V4.13.5 positive-Maker exit authority / Maker grace;
- V4.13.6 Kappa density scheduler + deep-EV prefilter;
- V4.13.7 qualified-Core exact-min recycle and stale-TTL rescue;
- V4.13.8 profitable Maker-exit persistence / queue-priority hold;
- hard NEGATIVE_EV, inventory, contract, post-only, volume and rescue safety.

AdaptiveAgent may tighten or reweight bounded outputs, but must never bypass these hard safety/economic contracts.
