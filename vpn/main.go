// NexSearch's application-only VPN. It never installs a TUN interface, advertises
// subnet routes, acts as an exit node, or opens a general-purpose SOCKS proxy.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tsnet"
)

type config struct {
	Mode       string `json:"mode"`
	Dir        string `json:"dir"`
	Hostname   string `json:"hostname"`
	Target     string `json:"target"`
	ControlURL string `json:"control_url"`
	Port       int    `json:"port"`
}
type status struct {
	Phase    string `json:"phase"`
	AuthURL  string `json:"auth_url,omitempty"`
	LocalURL string `json:"local_url,omitempty"`
	Address  string `json:"address,omitempty"`
	Error    string `json:"error,omitempty"`
}

var hostnamePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

func (c config) validate() error {
	if c.Mode != "host" && c.Mode != "employee" {
		return errors.New("invalid mode")
	}
	if c.Dir == "" || !hostnamePattern.MatchString(c.Hostname) || !hostnamePattern.MatchString(c.Target) {
		return errors.New("invalid identity")
	}
	u, err := url.Parse(c.ControlURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("control server must use HTTPS")
	}
	if c.Port < 1024 || c.Port > 65535 {
		return errors.New("invalid application port")
	}
	return nil
}

func peerAddress(peers map[string]*ipnstate.PeerStatus, hostname string) string {
	for _, peer := range peers {
		name := strings.Split(strings.TrimSuffix(peer.DNSName, "."), ".")[0]
		if !peer.Online || (peer.HostName != hostname && name != hostname) {
			continue
		}
		for _, address := range peer.TailscaleIPs {
			if address.Is4() {
				return address.String()
			}
		}
		if len(peer.TailscaleIPs) > 0 {
			return peer.TailscaleIPs[0].String()
		}
	}
	return ""
}

// copyDuplex keeps streams such as SSE open and supports TCP half-close.
func copyDuplex(a, b net.Conn) {
	defer a.Close()
	defer b.Close()
	done := make(chan struct{}, 2)
	copyOne := func(dst, src net.Conn) {
		_, _ = io.Copy(dst, src)
		if c, ok := dst.(interface{ CloseWrite() error }); ok {
			_ = c.CloseWrite()
		}
		done <- struct{}{}
	}
	go copyOne(a, b)
	go copyOne(b, a)
	<-done
	_ = a.SetDeadline(time.Now().Add(5 * time.Second))
	_ = b.SetDeadline(time.Now().Add(5 * time.Second))
	<-done
}

func forward(ctx context.Context, listener net.Listener, dial func(context.Context) (net.Conn, error)) {
	slots := make(chan struct{}, 32)
	go func() { <-ctx.Done(); _ = listener.Close() }()
	for {
		incoming, err := listener.Accept()
		if err != nil {
			return
		}
		select {
		case slots <- struct{}{}:
		default:
			_ = incoming.Close()
			continue
		}
		go func() {
			defer func() { <-slots }()
			connectionCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
			remote, err := dial(connectionCtx)
			cancel()
			if err != nil {
				_ = incoming.Close()
				return
			}
			copyDuplex(incoming, remote)
		}()
	}
}

func run(c config, commands *bufio.Scanner, emit func(status)) error {
	if err := c.validate(); err != nil {
		return err
	}
	if err := os.MkdirAll(c.Dir, 0700); err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	server := &tsnet.Server{Dir: c.Dir, Hostname: c.Hostname, ControlURL: c.ControlURL, Logf: func(string, ...any) {}, UserLogf: func(string, ...any) {}}
	if err := server.Start(); err != nil {
		return err
	}
	defer server.Close()
	client, err := server.LocalClient()
	if err != nil {
		return err
	}
	var targetLock sync.RWMutex
	destination := ""
	go func() {
		for commands.Scan() {
			switch strings.TrimSpace(commands.Text()) {
			case "stop":
				cancel()
				return
			case "login":
				_ = client.StartLoginInteractive(ctx)
			}
		}
		cancel()
	}()
	localURL := ""
	if c.Mode == "employee" {
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			return err
		}
		localURL = "https://" + listener.Addr().String()
		go forward(ctx, listener, func(dialCtx context.Context) (net.Conn, error) {
			targetLock.RLock()
			address := destination
			targetLock.RUnlock()
			if address == "" {
				return nil, errors.New("host offline")
			}
			return server.Dial(dialCtx, "tcp", net.JoinHostPort(address, strconv.Itoa(c.Port)))
		})
	} else {
		listener, err := server.Listen("tcp", ":"+strconv.Itoa(c.Port))
		if err != nil {
			return err
		}
		go forward(ctx, listener, func(dialCtx context.Context) (net.Conn, error) {
			var dialer net.Dialer
			return dialer.DialContext(dialCtx, "tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(c.Port)))
		})
	}
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		pollCtx, stop := context.WithTimeout(ctx, 5*time.Second)
		current, err := client.Status(pollCtx)
		stop()
		event := status{Phase: "connecting"}
		address := ""
		if err == nil {
			switch current.BackendState {
			case "NeedsLogin", "NoState":
				event.Phase = "needs_login"
				event.AuthURL = current.AuthURL
			case "NeedsMachineAuth":
				event.Phase = "needs_approval"
			case "Running":
				if c.Mode == "host" {
					event.Phase = "online"
					if len(current.TailscaleIPs) > 0 {
						event.Address = current.TailscaleIPs[0].String()
					}
				} else {
					peers := map[string]*ipnstate.PeerStatus{}
					for key, value := range current.Peer {
						peers[key.String()] = value
					}
					address = peerAddress(peers, c.Target)
					if address == "" {
						event.Phase = "host_offline"
					} else {
						event.Phase = "online"
						event.LocalURL = localURL
						event.Address = address
					}
				}
			}
		}
		targetLock.Lock()
		destination = address
		targetLock.Unlock()
		emit(event)
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}

func main() {
	log.SetOutput(io.Discard)
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), 65536)
	if !scanner.Scan() {
		return
	}
	var c config
	if json.Unmarshal(scanner.Bytes(), &c) != nil {
		fmt.Println(`{"phase":"error","error":"invalid configuration"}`)
		return
	}
	encoder := json.NewEncoder(os.Stdout)
	if err := run(c, scanner, func(s status) { _ = encoder.Encode(s) }); err != nil {
		_ = encoder.Encode(status{Phase: "error", Error: "VPN engine could not start"})
		os.Exit(1)
	}
}
