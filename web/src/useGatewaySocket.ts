import { useCallback, useEffect, useRef, useState } from "react";

import type { ConnectionState, GatewayMessage } from "./types";

type MessageHandler = (message: GatewayMessage) => void;

function socketUrl(): string {
  const configured = import.meta.env.VITE_GATEWAY_WS_URL;
  if (configured) return configured;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/chat`;
}

export function useGatewaySocket(onMessage: MessageHandler) {
  const [connection, setConnection] =
    useState<ConnectionState>("reconnecting");
  const socketRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onMessage);

  useEffect(() => {
    handlerRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    let stopped = false;
    let retry: number | undefined;

    const connect = () => {
      setConnection("reconnecting");
      const socket = new WebSocket(socketUrl());
      socketRef.current = socket;
      socket.onopen = () => setConnection("connected");
      socket.onmessage = (event) => {
        try {
          handlerRef.current(JSON.parse(event.data) as GatewayMessage);
        } catch {
          // Ignore malformed server frames; the next valid frame remains usable.
        }
      };
      socket.onerror = () => setConnection("offline");
      socket.onclose = () => {
        if (stopped) return;
        setConnection("offline");
        retry = window.setTimeout(connect, 1500);
      };
    };

    connect();
    return () => {
      stopped = true;
      if (retry !== undefined) window.clearTimeout(retry);
      socketRef.current?.close();
    };
  }, []);

  const sendTurn = useCallback(
    (requestId: string, sessionId: string, message: string, skillName?: string) => {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        throw new Error("Gateway 尚未连接，请稍后重试。");
      }
      socket.send(
        JSON.stringify({
          type: "run_turn",
          requestId,
          sessionId,
          message,
          ...(skillName ? { skillName } : {}),
        }),
      );
    },
    [],
  );

  return { connection, sendTurn };
}
