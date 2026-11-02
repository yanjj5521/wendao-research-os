package main

import (
    "context"
    "encoding/json"
    "errors"
    "flag"
    "fmt"
    "io"
    "log"
    "net"
    "os"
    "os/signal"
    "strconv"
    "strings"
    "syscall"
    "time"

    "tailscale.com/tsnet"
)

type readyMessage struct {
    State      string `json:"state"`
    Mode       string `json:"mode"`
    Hostname   string `json:"hostname"`
    ControlURL string `json:"control_url"`
    IP         string `json:"ip,omitempty"`
    URL        string `json:"url,omitempty"`
    Target     string `json:"target,omitempty"`
}

func emit(value any) {
    data, err := json.Marshal(value)
    if err != nil {
        return
    }
    fmt.Fprintln(os.Stdout, string(data))
    _ = os.Stdout.Sync()
}

func fail(format string, args ...any) {
    message := fmt.Sprintf(format, args...)
    emit(map[string]any{"state": "error", "error": message})
    log.Print(message)
    os.Exit(1)
}

func normalizeControlURL(value string) string {
    value = strings.TrimSpace(value)
    return strings.TrimRight(value, "/")
}

func proxyConnections(ctx context.Context, listener net.Listener, dial func(context.Context) (net.Conn, error)) {
    for {
        inbound, err := listener.Accept()
        if err != nil {
            select {
            case <-ctx.Done():
                return
            default:
                log.Printf("accept failed: %v", err)
                continue
            }
        }
        go func(inbound net.Conn) {
            defer inbound.Close()
            dialCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
            outbound, err := dial(dialCtx)
            cancel()
            if err != nil {
                log.Printf("dial failed: %v", err)
                return
            }
            defer outbound.Close()
            done := make(chan struct{}, 2)
            go func() {
                _, _ = io.Copy(outbound, inbound)
                if tcp, ok := outbound.(*net.TCPConn); ok {
                    _ = tcp.CloseWrite()
                }
                done <- struct{}{}
            }()
            go func() {
                _, _ = io.Copy(inbound, outbound)
                if tcp, ok := inbound.(*net.TCPConn); ok {
                    _ = tcp.CloseWrite()
                }
                done <- struct{}{}
            }()
            <-done
            <-done
        }(inbound)
    }
}

func hostPort(peer string, port int) string {
    peer = strings.TrimSpace(peer)
    peer = strings.TrimPrefix(peer, "http://")
    peer = strings.TrimPrefix(peer, "https://")
    peer = strings.TrimSuffix(peer, "/")
    if h, _, err := net.SplitHostPort(peer); err == nil {
        return h + ":" + strconv.Itoa(port)
    }
    return net.JoinHostPort(strings.Trim(peer, "[]"), strconv.Itoa(port))
}

func main() {
    mode := flag.String("mode", "", "serve or proxy")
    controlURL := flag.String("control-url", "", "Headscale control server URL")
    stateDir := flag.String("state-dir", "", "persistent tsnet state directory")
    hostname := flag.String("hostname", "", "node hostname in the Headscale network")
    target := flag.String("target", "", "serve mode backend address (for example 127.0.0.1:5050)")
    port := flag.Int("port", 5050, "serve mode virtual-network listen port")
    peer := flag.String("peer", "", "proxy mode Hub virtual IP or hostname")
    peerPort := flag.Int("peer-port", 5050, "proxy mode Hub virtual-network port")
    listen := flag.String("listen", "127.0.0.1:0", "proxy mode local loopback listen address")
    verbose := flag.Bool("verbose", false, "enable verbose tsnet logs")
    flag.Parse()

    if *mode != "serve" && *mode != "proxy" {
        fail("mode must be serve or proxy")
    }
    if normalizeControlURL(*controlURL) == "" {
        fail("control-url is required")
    }
    if strings.TrimSpace(*stateDir) == "" {
        fail("state-dir is required")
    }
    if strings.TrimSpace(*hostname) == "" {
        fail("hostname is required")
    }
    if *mode == "serve" && strings.TrimSpace(*target) == "" {
        fail("target is required in serve mode")
    }
    if *mode == "proxy" && strings.TrimSpace(*peer) == "" {
        fail("peer is required in proxy mode")
    }

    if err := os.MkdirAll(*stateDir, 0o700); err != nil {
        fail("create state directory: %v", err)
    }
    authKey := strings.TrimSpace(os.Getenv("HEADNET_AUTH_KEY"))
    if authKey == "" {
        fail("HEADNET_AUTH_KEY is required")
    }

    var debugLog = func(string, ...any) {}
    if *verbose {
        debugLog = log.New(os.Stderr, "tsnet: ", log.LstdFlags).Printf
    }
    srv := &tsnet.Server{
        Dir:        *stateDir,
        Hostname:   strings.TrimSpace(*hostname),
        AuthKey:    authKey,
        ControlURL: normalizeControlURL(*controlURL),
        UserLogf:   log.New(os.Stderr, "headnet: ", log.LstdFlags).Printf,
        Logf:       debugLog,
    }
    defer srv.Close()
    if err := srv.Start(); err != nil {
        fail("start tsnet: %v", err)
    }

    upCtx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
    _, err := srv.Up(upCtx)
    cancel()
    if err != nil {
        fail("join Headscale network: %v", err)
    }
    ip4, _ := srv.TailscaleIPs()
    if !ip4.IsValid() {
        fail("Headscale did not assign an IPv4 address")
    }

    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()

    switch *mode {
    case "serve":
        listener, err := srv.Listen("tcp", ":"+strconv.Itoa(*port))
        if err != nil {
            fail("listen on virtual network: %v", err)
        }
        defer listener.Close()
        emit(readyMessage{
            State: "ready", Mode: "serve", Hostname: *hostname, ControlURL: *controlURL,
            IP: ip4.String(), URL: fmt.Sprintf("http://%s:%d", ip4.String(), *port), Target: *target,
        })
        proxyConnections(ctx, listener, func(dialCtx context.Context) (net.Conn, error) {
            var dialer net.Dialer
            return dialer.DialContext(dialCtx, "tcp", *target)
        })
    case "proxy":
        localListener, err := net.Listen("tcp", *listen)
        if err != nil {
            fail("listen on loopback: %v", err)
        }
        defer localListener.Close()
        localAddr := localListener.Addr().(*net.TCPAddr)
        destination := hostPort(*peer, *peerPort)
        emit(readyMessage{
            State: "ready", Mode: "proxy", Hostname: *hostname, ControlURL: *controlURL,
            IP: ip4.String(), URL: fmt.Sprintf("http://127.0.0.1:%d", localAddr.Port), Target: destination,
        })
        proxyConnections(ctx, localListener, func(dialCtx context.Context) (net.Conn, error) {
            return srv.Dial(dialCtx, "tcp", destination)
        })
    default:
        fail(errors.New("unreachable mode").Error())
    }
}
