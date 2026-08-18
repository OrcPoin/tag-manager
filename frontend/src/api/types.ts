export type ProjectScan = {
  images: number;
  root_images: number;
  nested_images: number;
  captions: number;
  missing_captions: number;
  unsupported: number;
  scanned_at: string;
  signature: string;
};

export type Project = {
  id: string;
  name: string;
  dataset_path: string;
  status: string;
  updated_at: string;
  notes: string;
  settings: { include_subfolders?: boolean };
  attention_summary: Record<string, number>;
  last_scan: ProjectScan | null;
  active_recipe_id?: string | null;
  active_recipe_version?: number | null;
};

export type SystemStatus = { status: "ready" | "starting" | "failed"; schema_version: number; service_version: string; capabilities?: { inference: boolean; test_run: boolean } };

export type AutoPlan = {
  plan_id: string;
  project_id: string;
  scope: { create: number; overwrite: number; preserve: number; total: number };
  warnings: string[];
  blockers: string[];
  explanation: { model: string; resources: string; tradeoff: string; confidence: string };
  test_recommended: boolean;
  recipe_draft: Record<string, unknown>;
  model_snapshot: Record<string, unknown>;
  effective_resource_configuration: Record<string, unknown>;
};

export type InferenceMetrics = { activity:string|null; tokens_per_second:number|null; total_tokens:number|null; item_elapsed_seconds:number|null; elapsed_seconds:number|null; eta_seconds:number|null; vram_used_bytes:number|null; ram_used_bytes:number|null; context_used_tokens:number|null; context_limit_tokens:number|null; backend:string|null; model:string|null; resource_profile:string|null };
export type Run = { run_id:string; project_id:string; created_at:string; status:string; stage:string; scope_plan:Record<string,unknown>; progress:{done:number;total:number;current_image:string|null;errors:number;review_count:number;retries:number}; inference_metrics:InferenceMetrics; summary:Record<string,unknown> };
export type TestRunState = { status:string; run_id?:string; blocker?:string; sample_images:string[] };
export type ReviewItem = { id:string;project_id:string;run_id:string;image_relative_path:string;proposed_caption:string;reason_codes:string[];reasons:string[];status:string;user_note:string };
export type ProjectStorage = { event_bytes:number;event_segments:number;run_snapshots:number;summaries:number };
export type GalleryItem = { path:string;name:string;caption:string;has_caption:boolean };
export type GalleryPage = { items:GalleryItem[];page:number;page_size:number;total:number;pages:number };
export type VisualSearchResult = { references:string[]; items:Array<GalleryItem & {score:number;reason:string}>; total:number; index:{total:number;updated:number;cached:number;version:number} };
export type HealthReport = { project_id:string;images:number;captioned:number;total_bytes:number;issue_count:number;issues:Record<string,Array<string>|Array<string[]>> };
export type ResourceTagger = { id:string;name:string;installed:boolean;license:string;size_bytes:number;notes:string;repo_id:string };
export type VisualModelResource = { id:string;name:string;repo:string;license:string;files:string[];size_bytes:number;notes:string;installed:boolean };
export type ResourceInventory = { taggers:ResourceTagger[]; visual_models:VisualModelResource[] };
export type HardwareInfo = { logical_cores:number;physical_cores:number;ram_total_bytes:number;ram_available_bytes:number;gpus:Array<{name:string;backend:string;total_bytes:number;free_bytes:number;driver:string}> };
export type Recipe = { recipe_id:string;version:number;goal:string;result_type:string;status:string;origin_recipe_id?:string|null;prompt:string;instructions:string;generation_settings:Record<string,unknown> };
export type TaggerInfo = { id:string;name:string;installed:boolean };
export type RunComparison = { left_run_id:string;right_run_id:string;identical_configuration:boolean;sections:Record<string,Array<{path:string;left:unknown;right:unknown}>> };
