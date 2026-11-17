import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  ChevronRight,
  Leaf,
  LoaderCircle,
  MapPin,
  Menu,
  MessageSquare,
  Plus,
  Search,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
  PanelRightOpen,
  LogOut,
  Trees,
  Square,
} from "lucide-react";
import { api, cancelAnswer, imageUrl, streamAnswer } from "./api";
import { translate } from "./i18n";
import type {
  Answer,
  Language,
  Card,
  Conversation,
  Message,
  Picture,
  Session,
  Source,
} from "./types";

const suggestionsZh = [
  "三官殿的龙柏有多少岁？",
  "“清奇古怪”真是邓禹种的吗？",
  "书中哪些树已经不存了？",
];
const pages = (values: number[]) => values.join("、");
const focusedItem = (card: Card) =>
  card.items.find((i) => i.item_no === card.focus_item_no);
const cardTitle = (card: Card) =>
  focusedItem(card)?.species.join("、") || card.title;
const cardLocation = (card: Card) =>
  focusedItem(card)?.location || card.location;

function App() {
  const [lang, setLang] = useState<Language>(() =>
    localStorage.getItem("tree-chat-lang") === "en" ? "en" : "zh",
  );
  const t = (text: string) => translate(lang, text);
  const suggestions =
    lang === "en"
      ? [
          "How old is the Dragon Juniper at Sanguan Hall?",
          "Did Deng Yu really plant the four junipers at Situ Temple?",
          "Which trees in the book are no longer standing?",
        ]
      : suggestionsZh;
  const pageLabel = (value: number | number[]) =>
    lang === "en"
      ? `Book of Ancient Trees, p. ${Array.isArray(value) ? value.join(", ") : value}`
      : `《树碑立传》第 ${Array.isArray(value) ? pages(value) : value} 页`;
  useEffect(() => {
    localStorage.setItem("tree-chat-lang", lang);
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    document.title =
      lang === "en"
        ? "Shuyu · Ancient Trees of Wuzhong"
        : "树语 · 吴中古树问答";
  }, [lang]);
  const [session, setSession] = useState<Session | null>(null),
    [boot, setBoot] = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]),
    [cid, setCid] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]),
    [draft, setDraft] = useState(""),
    [busy, setBusy] = useState(false),
    [stage, setStage] = useState("");
  const [catalog, setCatalog] = useState<Card[]>([]),
    [catalogOpen, setCatalogOpen] = useState(false),
    [catalogQuery, setCatalogQuery] = useState(""),
    [district, setDistrict] = useState(""),
    [species, setSpecies] = useState(""),
    [speciesOptions, setSpeciesOptions] = useState<string[]>([]);
  const [streaming, setStreaming] = useState(false),
    [canStop, setCanStop] = useState(false),
    [stopping, setStopping] = useState(false);
  const [sources, setSources] = useState<Source[]>([]),
    [cards, setCards] = useState<Card[]>([]),
    [selected, setSelected] = useState<string | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false),
    [sidebarOpen, setSidebarOpen] = useState(false),
    [tab, setTab] = useState<"sources" | "trees">("sources");
  const [notice, setNotice] = useState(""),
    [password, setPassword] = useState(""),
    [feedback, setFeedback] = useState<Message | null>(null),
    [reason, setReason] = useState("事实不准确"),
    [note, setNote] = useState("");
  const [zoom, setZoom] = useState<Picture | null>(null);
  const end = useRef<HTMLDivElement>(null),
    textarea = useRef<HTMLTextAreaElement>(null),
    active = useRef<string | null>(null);
  const busyRef = useRef(false);
  const streamingRef = useRef(false);
  const sendVersion = useRef(0);
  const recoveringQuestion = useRef<{
    question: string;
    version: number;
  } | null>(null);
  const streamController = useRef<AbortController | null>(null);
  const initialized = useRef(false);

  useEffect(() => {
    const retryImages = () => {
      for (const image of document.images) {
        if (image.complete && !image.naturalWidth && image.src) {
          const url = new URL(image.src);
          if (url.origin === location.origin) {
            url.searchParams.set("retry", String(Date.now()));
            image.src = url.href;
          }
        }
      }
    };
    window.addEventListener("online", retryImages);
    return () => window.removeEventListener("online", retryImages);
  }, []);

  const error = (e: unknown) =>
    setNotice(e instanceof Error ? e.message : t("操作未完成，请重试。"));
  const selectResult = (result?: Answer) => {
    setSources(result?.sources || []);
    setCards(
      (result?.cards || []).map((card) => {
        const items = new Set(
          (result?.sources || [])
            .filter((s) => s.unit_id === card.unit_id)
            .map((s) => s.item_no),
        );
        if (items.size !== 1 || card.items.length <= 1) return card;
        const item = [...items][0];
        return {
          ...card,
          focus_item_no: item,
          images: card.images.filter((im) => im.item_no === item),
        };
      }),
    );
    setSelected(result?.sources?.[0]?.chunk_id || null);
  };
  const refreshConversations = async () =>
    setConversations(await api<Conversation[]>("/conversations"));
  const loadConversation = async (id: string) => {
    if (busyRef.current) return;
    const version = ++sendVersion.current;
    active.current = id;
    setCid(id);
    try {
      const items = await api<Message[]>(`/conversations/${id}/messages`);
      if (active.current !== id || sendVersion.current !== version) return;
      localStorage.setItem("tree-chat-id", id);
      setMessages(items);
      setSidebarOpen(false);
      selectResult(
        [...items].reverse().find((m) => m.role === "assistant")?.result,
      );
      const processing = items.some((m) => m.status === "processing");
      setBusy(processing);
      setCanStop(processing);
      busyRef.current = processing;
      if (processing) setStage(t("继续等待回答；可以关闭页面，结果会保留"));
    } catch (e) {
      if (active.current !== id || sendVersion.current !== version) return;
      error(e);
      localStorage.removeItem("tree-chat-id");
    }
  };
  const initialize = async () => {
    try {
      const s = await api<Session>("/session");
      setSession(s);
      if (s.authenticated) {
        const history = await api<Conversation[]>("/conversations");
        setConversations(history);
        const saved = localStorage.getItem("tree-chat-id");
        if (saved && history.some((c) => c.id === saved))
          await loadConversation(saved);
      }
    } catch (e) {
      error(e);
    } finally {
      setBoot(false);
    }
  };
  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      void initialize();
    }
  }, []); // Initialize one browser-owned workspace.
  useEffect(() => {
    if (!session?.authenticated) return;
    let disposed = false;
    setDistrict("");
    setSpecies("");
    void api<{ entries: Card[]; species: string[] }>(`/catalog?lang=${lang}`)
      .then((data) => {
        if (!disposed) {
          setCatalog(data.entries);
          setSpeciesOptions(data.species);
        }
      })
      .catch((e) => {
        if (!disposed) error(e);
      });
    return () => {
      disposed = true;
    };
  }, [lang, session?.authenticated]);
  const lastCompleteAssistant = [...messages]
    .reverse()
    .find((m) => m.role === "assistant" && m.status === "complete")?.id;
  useEffect(() => {
    const last = messages.at(-1);
    if (last?.role === "assistant" && last.status !== "processing") {
      end.current?.previousElementSibling?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    } else {
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages, stage]);
  useEffect(() => {
    if (!busy || !cid || streaming) return;
    let inFlight = false,
      disposed = false;
    const version = sendVersion.current;
    const timer = setInterval(async () => {
      if (streamingRef.current || inFlight) return;
      inFlight = true;
      try {
        const items = await api<Message[]>(`/conversations/${cid}/messages`);
        if (
          !disposed &&
          !streamingRef.current &&
          version === sendVersion.current &&
          active.current === cid &&
          !items.some((m) => m.status === "processing")
        ) {
          setMessages(items);
          const recovery = recoveringQuestion.current;
          if (recovery?.version === version) {
            if (
              items.filter((m) => m.role === "user").at(-1)?.text !==
              recovery.question
            )
              setDraft((prev) => prev || recovery.question);
            recoveringQuestion.current = null;
          }
          busyRef.current = false;
          setBusy(false);
          setCanStop(false);
          selectResult(
            [...items].reverse().find((m) => m.role === "assistant")?.result,
          );
          void refreshConversations();
        }
      } catch {
        /* The active stream reports connection failures. */
      } finally {
        inFlight = false;
      }
    }, 4000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [busy, cid, streaming]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 7000);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    function escape(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setZoom(null);
        setFeedback(null);
        setCatalogOpen(false);
        setEvidenceOpen(false);
        setSidebarOpen(false);
      }
    }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);

  useEffect(() => {
    if (!catalogOpen && !feedback && !zoom) return;
    const dialog = [
      ...document.querySelectorAll<HTMLElement>('[role="dialog"]'),
    ].at(-1);
    if (!dialog) return;
    const previous = document.activeElement as HTMLElement | null;
    const controls = () => [
      ...dialog.querySelectorAll<HTMLElement>(
        'button:not(:disabled),input,select,textarea,[tabindex="0"]',
      ),
    ];
    if (!dialog.contains(previous)) controls()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const list = controls(),
        first = list[0],
        last = list.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    dialog.addEventListener("keydown", trap);
    return () => {
      dialog.removeEventListener("keydown", trap);
      if (previous?.isConnected) previous.focus();
      else textarea.current?.focus();
    };
  }, [catalogOpen, feedback?.id, zoom?.image_id]);

  const newChat = () => {
    if (busyRef.current) return;
    active.current = null;
    sendVersion.current += 1;
    recoveringQuestion.current = null;
    setCid(null);
    setMessages([]);
    selectResult();
    setDraft("");
    setSidebarOpen(false);
    setEvidenceOpen(false);
    localStorage.removeItem("tree-chat-id");
    textarea.current?.focus();
  };
  const send = async (text = draft) => {
    const question = text.trim();
    if (!question || busyRef.current) return;
    busyRef.current = true;
    const version = ++sendVersion.current;
    recoveringQuestion.current = { question, version };
    const controller = new AbortController();
    streamController.current = controller;
    streamingRef.current = true;
    setStreaming(true);
    setCanStop(false);
    setBusy(true);
    setStage(t("准备检索资料"));
    setDraft("");
    setNotice("");
    let id = cid;
    let pending = false;
    let accepted = false;
    const current = () =>
      active.current === id && sendVersion.current === version;
    try {
      if (!id) {
        const c = await api<Conversation>("/conversations", {
          method: "POST",
          body: "{}",
        });
        id = c.id;
        active.current = id;
        setCid(id);
        localStorage.setItem("tree-chat-id", id);
      }
      setMessages((previous) => [
        ...previous,
        { id: "sending", role: "user", text: question, status: "complete" },
      ]);
      await streamAnswer(
        id,
        question,
        (event, data) => {
          if (!current()) return;
          if (event === "accepted") {
            accepted = true;
            setCanStop(true);
          }
          if (event === "progress") setStage((data as { stage: string }).stage);
          if (event === "result") {
            const message = data as Message;
            setMessages((previous) => [
              ...previous.filter((m) => m.status !== "processing"),
              message,
            ]);
            selectResult(message.result);
          }
        },
        controller.signal,
        lang,
      );
      const saved = await api<Message[]>(`/conversations/${id}/messages`);
      if (!current()) return;
      setMessages(saved);
      if (saved.filter((m) => m.role === "user").at(-1)?.text !== question)
        setDraft((prev) => prev || question);
      recoveringQuestion.current = null;
      await refreshConversations();
    } catch (e) {
      if (!current()) return;
      error(e);
      if (!accepted) setDraft((prev) => prev || question);
      // An interrupted connection can leave an accepted server task running.
      pending = !!id;
      if (id) {
        try {
          const saved = await api<Message[]>(`/conversations/${id}/messages`);
          if (!current()) return;
          setMessages(saved);
          pending = saved.some((m) => m.status === "processing");
          if (saved.filter((m) => m.role === "user").at(-1)?.text !== question)
            setDraft((prev) => prev || question);
          if (!pending) recoveringQuestion.current = null;
        } catch {
          /* Keep the visible question for recovery. */
        }
      } else setDraft((prev) => prev || question);
    } finally {
      if (current()) {
        streamingRef.current = false;
        setStreaming(false);
        busyRef.current = pending;
        setBusy(pending);
        setCanStop(pending);
        setStage(pending ? t("正在恢复连接，回答完成后会显示在这里") : "");
      }
    }
  };
  const stop = async () => {
    const id = active.current,
      version = sendVersion.current;
    if (!id || stopping) return;
    setStopping(true);
    try {
      await cancelAnswer(id);
      if (active.current !== id || sendVersion.current !== version) return;
      sendVersion.current += 1;
      recoveringQuestion.current = null;
      const stoppedVersion = sendVersion.current;
      streamController.current?.abort();
      streamingRef.current = false;
      setStreaming(false);
      const saved = await api<Message[]>(`/conversations/${id}/messages`);
      if (active.current !== id || sendVersion.current !== stoppedVersion)
        return;
      setMessages(saved);
      busyRef.current = false;
      setBusy(false);
      setCanStop(false);
      setStage("");
      void refreshConversations();
    } catch (e) {
      if (active.current === id) error(e);
    } finally {
      setStopping(false);
    }
  };
  const openSource = (answer: Answer, sourceId?: string) => {
    selectResult(answer);
    if (sourceId)
      setSelected(
        answer.sources?.find((s) => s.id === sourceId)?.chunk_id || null,
      );
    setTab("sources");
    setEvidenceOpen(true);
  };
  const openEntry = async (card: Card) => {
    try {
      const detail = await api<{ card: Card; sources: Source[] }>(
        `/entries/${card.entry_no}?lang=${lang}`,
      );
      setCards([detail.card]);
      setSources(detail.sources);
      setSelected(detail.sources[0]?.chunk_id || null);
      setTab("trees");
      setEvidenceOpen(true);
      setCatalogOpen(false);
    } catch (e) {
      error(e);
    }
  };
  const rate = async (message: Message, rating: string) => {
    try {
      await api(`/messages/${message.id}/feedback`, {
        method: "POST",
        body: JSON.stringify({
          rating,
          reason: rating === "bad" ? reason : "",
          note: rating === "bad" ? note : "",
        }),
      });
      setMessages((prev) =>
        prev.map((m) =>
          m.id === message.id
            ? {
                ...m,
                rating,
                reason: rating === "bad" ? reason : "",
                note: rating === "bad" ? note : "",
              }
            : m,
        ),
      );
      setFeedback(null);
      setNote("");
      setNotice(t("已记录反馈，供项目组检查。"));
    } catch (e) {
      error(e);
    }
  };
  const deleteConversation = async (id: string) => {
    if (busyRef.current) return;
    if (!window.confirm(t("删除这个对话及其反馈？此操作无法撤销。"))) return;
    try {
      await api(`/conversations/${id}`, { method: "DELETE", body: "{}" });
      if (cid === id) newChat();
      await refreshConversations();
    } catch (e) {
      error(e);
    }
  };
  const selectedSource =
    sources.find((s) => s.chunk_id === selected) || sources[0];
  const filteredCatalog = catalog.filter(
    (c) =>
      (!district || c.district === district) &&
      (!species || c.species.includes(species)) &&
      [
        c.location,
        ...c.items.map((item) => item.location),
        c.title,
        ...c.species,
        ...(c.official_codes || []),
        ...(c.location_aliases || []),
      ]
        .join(" ")
        .toLowerCase()
        .includes(catalogQuery.trim().toLowerCase()),
  );
  const heroCards = [1, 18, 21]
    .map((n) => catalog.find((c) => c.entry_no === n))
    .filter((c): c is Card => !!c);

  if (boot)
    return (
      <div className="loading-page">
        <Leaf size={34} />
        <p>{t("正在打开古树故事……")}</p>
      </div>
    );
  if (!session?.authenticated)
    return (
      <div className="login-page">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api("/login", {
                method: "POST",
                body: JSON.stringify({ password }),
              });
              setPassword("");
              await initialize();
            } catch (err) {
              error(err);
            }
          }}
        >
          <div className="brand-mark">
            <Leaf />
          </div>
          <p className="eyebrow">{t("吴中古树 · 内部工作区")}</p>
          <h1>{t("在这里，听树说话。")}</h1>
          <p>{t("输入项目访问口令，继续探索书中的故事。")}</p>
          <label htmlFor="password">{t("访问口令")}</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button className="primary" type="submit">
            {t("进入工作区")} <ArrowRight size={17} />
          </button>
          {notice && (
            <p role="alert" className="error">
              {notice}
            </p>
          )}
          {!session && (
            <button
              type="button"
              className="text-button"
              onClick={() => void initialize()}
            >
              {t("重新连接本地服务")}
            </button>
          )}
        </form>
      </div>
    );

  return (
    <div className="app-shell">
      {(sidebarOpen || evidenceOpen) && (
        <button
          className="mobile-shade"
          aria-label={t("关闭侧栏")}
          onClick={() => {
            setSidebarOpen(false);
            setEvidenceOpen(false);
          }}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? "is-open" : ""}`}>
        <button className="brand" onClick={newChat} disabled={busy}>
          <span className="brand-mark">
            <Leaf size={23} />
          </span>
          <span>
            <strong>{t("树语")}</strong>
            <small>{t("吴中古树问答")}</small>
          </span>
        </button>
        <button className="new-chat" onClick={newChat} disabled={busy}>
          <Plus size={17} /> {t("开启新的对话")}
        </button>
        <button
          className="catalog-nav"
          onClick={() => {
            setCatalogOpen(true);
            setSidebarOpen(false);
          }}
        >
          <BookOpen size={17} />
          <span>{t("古树目录")}</span>
          <span className="count">{catalog.length}</span>
        </button>
        <div className="history-label">
          {t("最近对话")} <span>{t("仅此浏览器可见")}</span>
        </div>
        <nav className="history" aria-label={t("最近对话")}>
          {conversations.length ? (
            conversations.map((c) => (
              <div
                className={`history-item ${cid === c.id ? "active" : ""}`}
                key={c.id}
              >
                <button
                  onClick={() => void loadConversation(c.id)}
                  disabled={busy}
                >
                  <MessageSquare size={14} />
                  <span>{c.title}</span>
                </button>
                <button
                  className="delete-chat"
                  aria-label={
                    lang === "en"
                      ? `Delete conversation: ${c.title}`
                      : `删除对话：${c.title}`
                  }
                  disabled={busy}
                  onClick={() => void deleteConversation(c.id)}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          ) : (
            <p className="empty-history">
              {t("你的每一次提问，")}
              <br />
              {t("都会留在这里。")}
            </p>
          )}
        </nav>
        <div className="sidebar-bottom">
          <span className="status-dot" />
          <span>{t("项目组工作区")}</span>
          <span className="version">V5.0</span>
        </div>
        <p className="sidebar-note">{t("依据《树碑立传》正文")}</p>
        {session.requires_password && (
          <button
            className="logout"
            onClick={async () => {
              await api("/logout", { method: "POST", body: "{}" });
              location.reload();
            }}
          >
            <LogOut size={14} />
            {t("退出工作区")}
          </button>
        )}
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="topbar-title">
            <button
              className="icon-button mobile-only"
              aria-label={t("打开对话导航")}
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={21} />
            </button>
            <span>{t("树的故事")}</span>
            <span className="slash">/</span>
            <span className="subtle">{t("与古树对话")}</span>
          </div>
          <div className="topbar-actions">
            <div
              className="language-toggle"
              role="group"
              aria-label="Language / 语言"
            >
              <button
                type="button"
                aria-pressed={lang === "zh"}
                onClick={() => setLang("zh")}
              >
                中
              </button>
              <button
                type="button"
                aria-pressed={lang === "en"}
                onClick={() => setLang("en")}
              >
                EN
              </button>
            </div>
            <button
              className="icon-button evidence-toggle"
              aria-label={t("打开原文出处")}
              onClick={() => setEvidenceOpen(true)}
            >
              <PanelRightOpen size={20} />
            </button>
          </div>
        </header>
        <div className="chat-scroll">
          {!messages.length ? (
            <section className="welcome">
              <div className="eyebrow">
                <span /> STORIES ROOTED IN WUZHONG
              </div>
              <h1>
                {t("一棵树，")}
                <br />
                {t("一段吴中往事。")}
              </h1>
              <p className="intro">
                {t("从山间古寺到寻常巷陌，")}
                <br className="mobile-only" />
                {t("循着书中记载，听见古树的回声。")}
              </p>
              <div className="hero-cards">
                {heroCards.map((card, i) => (
                  <button
                    key={card.unit_id}
                    onClick={() => void openEntry(card)}
                    className="hero-card"
                  >
                    <img
                      src={imageUrl(
                        card.images.find((im) => im.kind === "photo")?.url,
                      )}
                      alt={`${card.location} · ${card.title}`}
                      loading="eager"
                    />
                    <div className="hero-card-overlay" />
                    <div className="hero-card-caption">
                      <span>
                        0{i + 1} / {card.location}
                      </span>
                      <strong>
                        {i === 1
                          ? lang === "en"
                            ? "Qing · Qi · Gu · Guai"
                            : "清 · 奇 · 古 · 怪"
                          : card.title}
                      </strong>
                      <ArrowRight size={17} />
                    </div>
                  </button>
                ))}
              </div>
              <div className="starter-title">
                {t("从一个问题开始")} <span>{t("每个回答，都可回到原文")}</span>
              </div>
              <div className="suggestions">
                {suggestions.map((q, i) => (
                  <button key={q} onClick={() => void send(q)} disabled={busy}>
                    <span className="suggestion-index">0{i + 1}</span>
                    <span>{q}</span>
                    <ArrowRight size={16} />
                  </button>
                ))}
              </div>
            </section>
          ) : (
            <div className="messages" aria-live="polite">
              {messages.map((message) => (
                <article key={message.id} className={`message ${message.role}`}>
                  {message.role === "user" ? (
                    <div className="user-bubble">{message.text}</div>
                  ) : (
                    <>
                      <div className="assistant-heading">
                        <span className="assistant-mark">
                          <Leaf size={18} />
                        </span>
                        <strong>{t("树语")}</strong>
                        <span>{t("循原文而答")}</span>
                      </div>
                      <div
                        className={`answer-body ${message.status === "error" ? "error-answer" : ""}`}
                      >
                        {(message.result?.text || message.text) && (
                          <p>{message.result?.text || message.text}</p>
                        )}
                        {message.result?.claims?.map((claim, i) => (
                          <div className="answer-section" key={i}>
                            {claim.heading && <h3>{claim.heading}</h3>}
                            <p
                              className={
                                message.result?.structured ? "list-claim" : ""
                              }
                            >
                              {message.result?.structured && (
                                <span className="list-number">{i + 1}.</span>
                              )}
                              {claim.text}
                              <span className="citation-group">
                                {claim.source_ids.map((sid) => {
                                  const source = message.result?.sources?.find(
                                    (s) => s.id === sid,
                                  );
                                  return (
                                    <button
                                      key={sid}
                                      className="citation"
                                      aria-label={
                                        lang === "en"
                                          ? `View source ${sid}, page ${pages(source?.printed_pages || [])}`
                                          : `查看出处 ${sid}，第${pages(source?.printed_pages || [])}页`
                                      }
                                      onClick={() =>
                                        openSource(message.result!, sid)
                                      }
                                    >
                                      {sid.replace("S", "")}
                                    </button>
                                  );
                                })}
                              </span>
                            </p>
                            {!!claim.images?.length && (
                              <div
                                className={`answer-images ${claim.images.length === 1 ? "single-image" : ""}`}
                              >
                                {claim.images.map((picture) => (
                                  <figure key={picture.image_id}>
                                    <button
                                      className="answer-image-button"
                                      aria-label={
                                        lang === "en"
                                          ? `Enlarge image: ${picture.display_caption || pageLabel(picture.printed_page)}`
                                          : `放大回答配图：${picture.display_caption || `第${picture.printed_page}页`}`
                                      }
                                      onClick={() => setZoom(picture)}
                                    >
                                      <img
                                        src={imageUrl(picture.url)}
                                        width={picture.width}
                                        height={picture.height}
                                        alt={
                                          picture.display_caption ||
                                          picture.caption ||
                                          pageLabel(picture.printed_page)
                                        }
                                        loading="lazy"
                                      />
                                      <span>{t("点击查看大图")}</span>
                                    </button>
                                    <figcaption>
                                      <span>
                                        {picture.display_caption ||
                                          picture.caption ||
                                          t("书内配图")}
                                      </span>
                                      <small>
                                        {pageLabel(picture.printed_page)}
                                        {picture.credit
                                          ? ` · ${picture.credit}`
                                          : ""}
                                      </small>
                                    </figcaption>
                                  </figure>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                        {message.status === "processing" && (
                          <p className="progress">
                            <LoaderCircle size={16} className="spin" />
                            {t("回答正在生成……")}
                          </p>
                        )}
                      </div>
                      {message.result?.retrieval_notice && (
                        <p className="answer-notice">
                          {message.result.retrieval_notice}
                        </p>
                      )}
                      {message.result?.sources?.length ? (
                        <button
                          className="source-summary"
                          onClick={() => openSource(message.result!)}
                        >
                          <BookOpen size={14} />
                          {lang === "en"
                            ? `${message.result.sources.length} passages · View sources`
                            : `${message.result.sources.length} 段原文 · 点击核对出处`}
                          <ChevronRight size={15} />
                        </button>
                      ) : null}
                      {message.status === "complete" && (
                        <div className="answer-actions">
                          <button
                            aria-label={t("回答有帮助")}
                            title={t("回答有帮助")}
                            className={message.rating === "good" ? "rated" : ""}
                            onClick={() => void rate(message, "good")}
                          >
                            <ThumbsUp size={15} />
                          </button>
                          <button
                            aria-label={t("反馈回答问题")}
                            title={t("反馈回答问题")}
                            className={message.rating === "bad" ? "rated" : ""}
                            onClick={() => {
                              setFeedback(message);
                              setNote(message.note || "");
                              setReason(message.reason || "事实不准确");
                            }}
                          >
                            <ThumbsDown size={15} />
                          </button>
                          {message.rating && <span>{t("已记录反馈")}</span>}
                        </div>
                      )}
                      {message.id === lastCompleteAssistant &&
                        !!message.result?.suggested_questions?.length && (
                          <div
                            className="followup-chips"
                            aria-label={t("继续探索")}
                          >
                            {message.result.suggested_questions.map((q) => (
                              <button
                                key={q}
                                disabled={busy}
                                onClick={() => void send(q)}
                              >
                                <span>{q}</span>
                                <ArrowRight size={14} />
                              </button>
                            ))}
                          </div>
                        )}
                      {message.status === "error" && (
                        <button
                          className="text-button"
                          onClick={() => {
                            const index = messages.findIndex(
                              (m) => m.id === message.id,
                            );
                            const q = messages
                              .slice(0, index)
                              .reverse()
                              .find((m) => m.role === "user");
                            if (q) void send(q.text);
                          }}
                          disabled={busy}
                        >
                          {t("重新尝试")}
                        </button>
                      )}
                    </>
                  )}
                </article>
              ))}
              {busy && (
                <div className="progress" role="status">
                  <LoaderCircle size={17} className="spin" />
                  {t(stage || "正在核对资料")}
                  <span className="progress-dots">···</span>
                </div>
              )}
              <div ref={end} />
            </div>
          )}
        </div>
        <div className="composer-wrap">
          <form
            className={`composer ${busy ? "working" : ""}`}
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <textarea
              ref={textarea}
              aria-label={t("向树语提问")}
              placeholder={t("想了解哪一棵树，或哪一段故事？")}
              value={draft}
              maxLength={1200}
              rows={2}
              disabled={busy}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="composer-bottom">
              <span>
                <BookOpen size={13} /> {t("《树碑立传》 · 树的故事")}
              </span>
              {busy ? (
                <button
                  className="send"
                  type="button"
                  aria-label={t("停止回答")}
                  title={t("停止回答")}
                  disabled={!canStop || stopping}
                  onClick={() => void stop()}
                >
                  <Square size={17} />
                </button>
              ) : (
                <button
                  className="send"
                  type="submit"
                  aria-label={t("发送问题")}
                  disabled={busy || !draft.trim()}
                >
                  {busy ? (
                    <LoaderCircle size={18} className="spin" />
                  ) : (
                    <ArrowUp size={19} />
                  )}
                </button>
              )}
            </div>
          </form>
          <div className="composer-note">{t("回答以书中正文为依据。")}</div>
        </div>
      </main>

      <aside
        className={`evidence ${evidenceOpen ? "is-open" : ""}`}
        aria-label={t("原文与古树资料")}
      >
        <div className="evidence-header">
          <BookOpen size={17} />
          <strong>{t("让每句话有出处")}</strong>
          <button
            className="icon-button close-evidence"
            aria-label={t("关闭原文面板")}
            onClick={() => setEvidenceOpen(false)}
          >
            <X size={19} />
          </button>
        </div>
        {sources.length || cards.length ? (
          <>
            <div className="evidence-tabs">
              <button
                className={tab === "sources" ? "selected" : ""}
                onClick={() => setTab("sources")}
              >
                {t("原文出处")} <span>{sources.length}</span>
              </button>
              <button
                className={tab === "trees" ? "selected" : ""}
                onClick={() => setTab("trees")}
              >
                {t("相关古树")} <span>{cards.length}</span>
              </button>
            </div>
            <div className="evidence-scroll">
              {tab === "sources" && (
                <>
                  {sources.length > 1 && (
                    <select
                      aria-label={t("选择原文段落")}
                      value={selectedSource?.chunk_id || ""}
                      onChange={(e) => setSelected(e.target.value)}
                    >
                      {sources.map((s) => (
                        <option key={s.chunk_id} value={s.chunk_id}>
                          {s.id} · {s.type_label} ·{" "}
                          {lang === "en"
                            ? `p. ${s.printed_pages.join(", ")}`
                            : `第${pages(s.printed_pages)}页`}
                        </option>
                      ))}
                    </select>
                  )}
                  {selectedSource && (
                    <article className="source-detail">
                      <div className="source-kicker">
                        {selectedSource.type_label}
                      </div>
                      <h2>{selectedSource.title}</h2>
                      <p className="source-location">
                        <MapPin size={13} />
                        {selectedSource.location}
                      </p>
                      <div className="page-label">
                        {selectedSource.citation}
                      </div>
                      <p className="original-text">{selectedSource.text}</p>
                      {selectedSource.text_zh && (
                        <details className="original-translation">
                          <summary>{t("查看中文原文")}</summary>
                          <p className="original-text" lang="zh-CN">
                            {selectedSource.text_zh}
                          </p>
                        </details>
                      )}
                      <div className="source-footer">
                        {selectedSource.text_zh
                          ? t(
                              selectedSource.translation_status ===
                                "human_approved"
                                ? "译文已由人工签审"
                                : selectedSource.translation_status ===
                                    "ai_reviewed"
                                  ? "AI 已逐段审校 · 未经人工签审"
                                  : "机器翻译 · 尚未审校",
                            )
                          : t("原文照录 · 页码为书内印刷页码")}
                      </div>
                      <button
                        className="text-button"
                        onClick={() => setTab("trees")}
                      >
                        {t("查看相关古树")} <ArrowRight size={14} />
                      </button>
                    </article>
                  )}
                </>
              )}
              {tab === "trees" &&
                cards.map((card) => (
                  <article className="tree-detail" key={card.unit_id}>
                    {card.images[0] && (
                      <button
                        className="tree-image-button"
                        aria-label={
                          lang === "en"
                            ? `Enlarge ${cardTitle(card)} image`
                            : `放大${cardTitle(card)}图片`
                        }
                        onClick={() => setZoom(card.images[0])}
                      >
                        <img
                          src={imageUrl(card.images[0].url)}
                          alt={`${cardLocation(card)} ${cardTitle(card)}`}
                        />
                      </button>
                    )}
                    <span className="source-kicker">
                      {t("故事")} {String(card.entry_no).padStart(3, "0")} ·{" "}
                      {card.district}
                    </span>
                    <h2>{cardTitle(card)}</h2>
                    {card.hook && <p className="tree-hook">{card.hook}</p>}
                    <p className="source-location">
                      <MapPin size={14} />
                      {cardLocation(card)}
                    </p>
                    {card.items.length > 1 && (
                      <div className="item-list">
                        {card.items.map((it) => (
                          <p key={it.item_no}>
                            <strong>{it.species.join("、")}</strong>
                            <br />
                            {it.location}
                          </p>
                        ))}
                      </div>
                    )}
                    {card.images.length > 1 && (
                      <div className="image-strip">
                        {card.images.slice(1).map((im) => (
                          <button
                            aria-label={
                              lang === "en"
                                ? `Enlarge book image, page ${im.printed_page}`
                                : `放大书内第${im.printed_page}页图片`
                            }
                            key={im.image_id}
                            onClick={() => setZoom(im)}
                          >
                            <img
                              src={imageUrl(im.url)}
                              alt={
                                im.display_caption || pageLabel(im.printed_page)
                              }
                              loading="lazy"
                            />
                          </button>
                        ))}
                      </div>
                    )}
                    <button
                      className="ask-tree"
                      disabled={busy}
                      onClick={() => {
                        setDraft(
                          lang === "en"
                            ? `Tell me about the ${cardTitle(card)} at ${cardLocation(card)}.`
                            : `介绍一下${cardLocation(card)}的${cardTitle(card)}`,
                        );
                        setEvidenceOpen(false);
                        textarea.current?.focus();
                      }}
                    >
                      {t("问问这棵树")} <ArrowRight size={14} />
                    </button>
                    <p className="image-credit">{t("图像来自《树碑立传》")}</p>
                  </article>
                ))}
            </div>
          </>
        ) : (
          <div className="evidence-empty">
            <div className="book-illustration">
              <Trees size={56} strokeWidth={0.8} />
              <span>{t("树 碑 立 传")}</span>
              <small>{t("古树名木的故事")}</small>
            </div>
            <h2>{t("一页原文，一份依据。")}</h2>
            <p>
              {t("提问后，在这里查看")}
              <br />
              {t("相关原文、书中页码与古树图片。")}
            </p>
            <div className="source-principles">
              <span>
                <Check size={14} /> {t("正文优先，保留原意")}
              </span>
              <span>
                <Check size={14} /> {t("传说与史载，分别说明")}
              </span>
              <span>
                <Check size={14} /> {t("资料不足时，如实告知")}
              </span>
            </div>
            <button
              className="text-button"
              onClick={() => setCatalogOpen(true)}
            >
              {t("先翻翻古树目录")} <ArrowRight size={14} />
            </button>
          </div>
        )}
        <div className="evidence-footnote">
          {t("资料来源 / 《树碑立传》树的故事")}
        </div>
      </aside>

      {catalogOpen && (
        <div className="modal-shade">
          <section
            className="catalog-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("古树目录")}
          >
            <div className="modal-heading">
              <div>
                <p className="eyebrow">{t("翻开古树的故事")}</p>
                <h2>
                  {t("古树目录")}{" "}
                  <span>
                    {filteredCatalog.length}{" "}
                    {lang === "en" ? "entries" : "个条目"}
                  </span>
                </h2>
              </div>
              <button
                className="icon-button"
                aria-label={t("关闭古树目录")}
                onClick={() => setCatalogOpen(false)}
              >
                <X />
              </button>
            </div>
            <div className="catalog-filters">
              <label className="search-field">
                <Search size={17} />
                <input
                  autoFocus
                  placeholder={t("搜索树名、地点或编号")}
                  aria-label={t("搜索古树目录")}
                  value={catalogQuery}
                  onChange={(e) => setCatalogQuery(e.target.value)}
                />
              </label>
              <select
                aria-label={t("按区县筛选")}
                value={district}
                onChange={(e) => setDistrict(e.target.value)}
              >
                <option value="">{t("全部区县")}</option>
                {[...new Set(catalog.map((c) => c.district))].map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
              <select
                aria-label={t("按树种筛选")}
                value={species}
                onChange={(e) => setSpecies(e.target.value)}
              >
                <option value="">{t("全部树种")}</option>
                {speciesOptions.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </div>
            <div className="catalog-grid">
              {filteredCatalog.map((card) => (
                <button
                  className="catalog-card"
                  key={card.unit_id}
                  onClick={() => void openEntry(card)}
                >
                  {card.images[0] && (
                    <img
                      src={imageUrl(card.images[0].url)}
                      alt=""
                      loading="lazy"
                    />
                  )}
                  <div>
                    <span>
                      {String(card.entry_no).padStart(3, "0")} / {card.district}
                    </span>
                    <h3>{card.title}</h3>
                    <p>{card.location}</p>
                    {card.hook && <p className="catalog-hook">{card.hook}</p>}
                  </div>
                  <ChevronRight size={16} />
                </button>
              ))}
              {!filteredCatalog.length && (
                <p className="empty-search">
                  {t("没有找到匹配的树名或地点。")}
                </p>
              )}
            </div>
          </section>
        </div>
      )}
      {feedback && (
        <div className="modal-shade">
          <form
            className="feedback-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("反馈回答问题")}
            onSubmit={(e) => {
              e.preventDefault();
              void rate(feedback, "bad");
            }}
          >
            <div className="modal-heading">
              <h2>{t("帮助我们核对回答")}</h2>
              <button
                className="icon-button"
                type="button"
                aria-label={t("关闭反馈")}
                onClick={() => setFeedback(null)}
              >
                <X size={20} />
              </button>
            </div>
            <label htmlFor="reason">{t("问题类型")}</label>
            <select
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            >
              {[
                "事实不准确",
                "引用不支持结论",
                "答非所问",
                "遗漏或过度推断",
                "其他问题",
              ].map((r) => (
                <option key={r} value={r}>
                  {t(r)}
                </option>
              ))}
            </select>
            <label htmlFor="feedback-note">{t("补充说明（可选）")}</label>
            <textarea
              id="feedback-note"
              value={note}
              maxLength={1500}
              rows={4}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("哪一处需要修改？")}
            />
            <button className="primary" type="submit">
              {t("记录反馈")} <Check size={16} />
            </button>
          </form>
        </div>
      )}
      {zoom && (
        <div
          className="modal-shade image-modal"
          role="dialog"
          aria-modal="true"
          aria-label={t("书内图片")}
        >
          <button
            className="image-close icon-button"
            aria-label={t("关闭放大图片")}
            onClick={() => setZoom(null)}
          >
            <X />
          </button>
          <img
            src={imageUrl(zoom.url)}
            alt={
              zoom.display_caption ||
              zoom.caption ||
              pageLabel(zoom.printed_page)
            }
          />
          <p>
            {pageLabel(zoom.printed_page)}
            {zoom.display_caption || zoom.caption
              ? ` · ${zoom.display_caption || zoom.caption}`
              : ""}
            {zoom.credit ? ` · ${zoom.credit}` : ""}
            <br />
            {t("《树碑立传》书内图片")}
          </p>
        </div>
      )}
      {notice && (
        <div className="toast" role="alert">
          {notice}
          <button aria-label={t("关闭提示")} onClick={() => setNotice("")}>
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
export default App;
