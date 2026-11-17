export type Language = "zh" | "en";
export interface Picture {
  image_id: string;
  url: string;
  printed_page: number;
  kind: string;
  caption?: string;
  credit?: string;
  rights?: { status: string };
  item_no?: number;
  display_caption?: string;
  width?: number;
  height?: number;
}
export interface Card {
  lang?: Language;
  hook?: string;
  followups?: string[];
  unit_id: string;
  entry_no: number;
  title: string;
  location: string;
  district: string;
  species: string[];
  official_codes?: string[];
  location_aliases?: string[];
  status: string;
  images: Picture[];
  printed_pages: number[];
  as_of: string;
  focus_item_no?: number;
  items: {
    item_no: number;
    location: string;
    species: string[];
    status: string;
  }[];
}
export interface Source {
  text_zh?: string;
  translation_status?: "machine" | "ai_reviewed" | "human_approved";
  id: string;
  chunk_id: string;
  unit_id: string;
  entry_no: number;
  item_no: number;
  title: string;
  location: string;
  content_type: string;
  type_label: string;
  text: string;
  context_header: string;
  printed_pages: number[];
  citation: string;
  as_of: string;
  certainty_flags: string[];
}
export interface Answer {
  lang?: Language;
  suggested_questions?: string[];
  suggested_question_ids?: string[];
  status: string;
  text: string;
  claims?: {
    heading?: string;
    text: string;
    source_ids: string[];
    images?: Picture[];
  }[];
  sources?: Source[];
  cards?: Card[];
  retrieval_notice?: string;
  audit?: { duration_ms: number; model_calls: unknown[] };
  structured?: { item_count: number; entry_count: number };
}
export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  status: string;
  result?: Answer;
  rating?: string;
  reason?: string;
  note?: string;
}
export interface Conversation {
  id: string;
  title: string;
  updated: number;
}
export interface Session {
  authenticated: boolean;
  requires_password: boolean;
  model_configured: boolean;
  counts?: { entries: number; items: number; chunks: number; images: number };
  as_of: string;
  model?: string;
}
