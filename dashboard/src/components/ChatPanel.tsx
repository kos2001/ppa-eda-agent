import { useState } from "react";
import {
  getStoredKey,
  setStoredKey,
  clearStoredKey,
  sendChatMessage,
  type ChatMessage,
} from "../api/gateway";
import "./Tabs.css";
import "./ChatPanel.css";

export default function ChatPanel() {
  const [key, setKey] = useState<string | null>(getStoredKey());
  const [keyInput, setKeyInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleSaveKey() {
    if (!keyInput.trim()) return;
    setStoredKey(keyInput.trim());
    setKey(keyInput.trim());
    setKeyInput("");
  }

  function handleClearKey() {
    clearStoredKey();
    setKey(null);
  }

  async function handleSend() {
    if (!input.trim() || !key) return;
    const next = [...messages, { role: "user" as const, content: input }];
    setMessages(next);
    setInput("");
    setSending(true);
    setError(null);
    try {
      const reply = await sendChatMessage(key, next);
      setMessages([...next, { role: "assistant", content: reply }]);
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  }

  if (!key) {
    return (
      <div className="tab">
        <div className="panel">
          <span className="panel__title">hermes ppa-agent — setup</span>
          <div className="panel__body chat-setup">
            <p>Enter your hermes-gateway client key to chat with ppa-agent.</p>
            <p className="chat-setup__hint">
              Stored only in this browser's localStorage — never sent
              anywhere except the gateway itself.
            </p>
            <input
              type="password"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              placeholder="gw-..."
            />
            <div className="report-input__actions">
              <button onClick={handleSaveKey}>Save key</button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="tab">
      <div className="panel chat-panel">
        <span className="panel__title">hermes ppa-agent — live chat</span>
        <div className="chat-panel__messages">
          {messages.length === 0 && (
            <div className="chat-message chat-message--hint">
              Ask about the ppa-agent build pipeline, Docker environment, or
              anything ppa-eda-analyst-adjacent.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`chat-message chat-message--${m.role}`}>
              <strong>{m.role === "user" ? "You" : "ppa-agent"}:</strong>{" "}
              {m.content}
            </div>
          ))}
          {sending && (
            <div className="chat-message chat-message--hint">
              ppa-agent is typing…
            </div>
          )}
          {error && <div className="tab__error">{error}</div>}
        </div>
        <div className="chat-panel__input">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Ask ppa-agent…"
            disabled={sending}
          />
          <button onClick={handleSend} disabled={sending || !input.trim()}>
            Send
          </button>
          <button onClick={handleClearKey} className="chat-panel__clear-key">
            Clear key
          </button>
        </div>
      </div>
    </div>
  );
}
