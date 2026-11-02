# headnet

`headnet` is the userspace virtual-network helper used by 问道科研 for a
self-hosted Headscale control plane.

It is built from Go with `tailscale.com/tsnet v1.104.4`:

```powershell
python tools/build_headnet.py
```

Modes:

```text
headnet --mode serve  # publish a local TCP service on the Headscale tailnet
headnet --mode proxy  # expose a remote tailnet service on 127.0.0.1
```

The auth key is passed through `HEADNET_AUTH_KEY`, never as a command-line
argument. Generated binaries are intentionally ignored by Git and bundled into
the Windows release by `tools/build.py`.

The helper has no system service, TUN adapter, or VPN installation side effect.
It uses tsnet's userspace networking and only listens on a Headscale interface
or a loopback address.

See `../../docs/HEADSCALE.md` for server and client setup.
