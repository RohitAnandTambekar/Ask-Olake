export type Status = "answered" | "partial" | "abstained" | "error";

export interface Source {
  n: number;
  title: string;
  url: string;
  breadcrumb: string[];
  source_type: string; // doc | release-note | blog | code | config | readme
  product: string;
  trust_tier: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  pending?: boolean;
  status?: Status;
  sources?: Source[];
  citations?: number[];
  warnings?: string[];
  notices?: string[];
  queryId?: string;
  feedback?: 1 | -1;
}

export type ServerEvent =
  | { event: "meta"; data: { query_id: string; notices: string[] } }
  | { event: "sources"; data: { sources: Source[] } }
  | { event: "delta"; data: { text: string } }
  | {
      event: "final";
      data: { status: Status; answer: string; citations: number[]; warnings: string[] };
    }
  | { event: "error"; data: { message: string } };
