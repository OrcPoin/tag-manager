import type { AutoPlan, GalleryPage, HardwareInfo, HealthReport, Project, ProjectStorage, Recipe, ResourceInventory, ReviewItem, Run, RunComparison, SystemStatus, TaggerInfo, TestRunState, VisualSearchResult } from "./types";

const API_ROOT = import.meta.env.VITE_API_ROOT ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail?.message ?? `Ошибка сервиса (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  recipes:(projectId:string)=>request<Recipe[]>(`/api/projects/${projectId}/recipes`),
  taggers:()=>request<TaggerInfo[]>("/api/taggers"),
  saveRecipe:(projectId:string,body:Record<string,unknown>)=>request<Recipe>(`/api/projects/${projectId}/recipes`,{method:"POST",body:JSON.stringify(body)}),
  compareRecipes:(projectId:string,recipeId:string,left:number,right:number)=>request<{identical:boolean,differences:Array<{path:string,left:unknown,right:unknown}>}>(`/api/projects/${projectId}/recipes/compare/${encodeURIComponent(recipeId)}/${left}/${right}`),
  activateRecipe:(projectId:string,recipeId:string,version:number)=>request<Recipe>(`/api/projects/${projectId}/recipes/${encodeURIComponent(recipeId)}/${version}/active`,{method:"POST"}),
  cloneRecipe:(projectId:string,recipeId:string,version:number,name:string)=>request<Recipe>(`/api/projects/${projectId}/recipes/${encodeURIComponent(recipeId)}/${version}/clone`,{method:"POST",body:JSON.stringify({name})}),
  archiveRecipe:(projectId:string,recipeId:string,version:number)=>request<Recipe>(`/api/projects/${projectId}/recipes/${encodeURIComponent(recipeId)}/${version}/archive`,{method:"POST"}),
  deleteRecipe:(projectId:string,recipeId:string,version:number)=>request<{deleted:boolean}>(`/api/projects/${projectId}/recipes/${encodeURIComponent(recipeId)}/${version}`,{method:"DELETE"}),
  status: () => request<SystemStatus>("/api/system/status"),
  projects: () => request<Project[]>("/api/projects"),
  openProject: (datasetPath: string) => request<Project>("/api/projects/open", {
    method: "POST",
    body: JSON.stringify({ dataset_path: datasetPath }),
  }),
  scanProject: (projectId: string, includeSubfolders?: boolean) => request<Project>(`/api/projects/${projectId}/scan`, { method: "POST", body: JSON.stringify({ include_subfolders: includeSubfolders }) }),
  gallery:(projectId:string,search="",missingOnly=false,page=1,pageSize=60)=>request<GalleryPage>(`/api/projects/${projectId}/gallery?search=${encodeURIComponent(search)}&missing_only=${missingOnly}&page=${page}&page_size=${pageSize}`),
  saveCaption:(projectId:string,path:string,caption:string)=>request<{path:string;caption:string;has_caption:boolean}>(`/api/projects/${projectId}/caption?path=${encodeURIComponent(path)}`,{method:"PUT",body:JSON.stringify({caption})}),
  regenerateGalleryItem:(projectId:string,path:string,feedback:string)=>request<{run_id:string;status:string}>(`/api/projects/${projectId}/gallery/regenerate`,{method:"POST",body:JSON.stringify({path,feedback})}),
  regenerateGalleryItems:(projectId:string,paths:string[],feedback:string)=>request<{run_id:string;status:string}>(`/api/projects/${projectId}/gallery/regenerate`,{method:"POST",body:JSON.stringify({paths,feedback})}),
  galleryBulk:(projectId:string,paths:string[],action:"add_tag"|"remove_tag"|"clear_caption",value="")=>request<{updated:Array<{path:string;caption:string;has_caption:boolean}>}>(`/api/projects/${projectId}/gallery/bulk`,{method:"POST",body:JSON.stringify({paths,action,value})}),
  projectHealth:(projectId:string)=>request<HealthReport>(`/api/projects/${projectId}/health`,{method:"POST"}),
  visualSearch:(projectId:string,references:string[],limit=100,threshold=0.8,mode="overall",query="")=>request<VisualSearchResult>(`/api/projects/${projectId}/visual-search`,{method:"POST",body:JSON.stringify({references,limit,threshold,mode,query})}),
  rebuildVisualSearchIndex:(projectId:string)=>request<{total:number;updated:number;cached:number}>(`/api/projects/${projectId}/visual-search/index`,{method:"POST"}),
  resources:()=>request<ResourceInventory>("/api/system/resources"),
  installTagger:(id:string)=>request<{id:string;installed:boolean}>(`/api/system/taggers/${encodeURIComponent(id)}/install`,{method:"POST",body:JSON.stringify({confirmed:true})}),
  removeTagger:(id:string)=>request<{id:string;installed:boolean}>(`/api/system/taggers/${encodeURIComponent(id)}`,{method:"DELETE"}),
  installVisualModel:(id:string)=>request<{id:string;installed:boolean}>(`/api/system/visual-models/${encodeURIComponent(id)}/install`,{method:"POST",body:JSON.stringify({confirmed:true})}),
  removeVisualModel:(id:string)=>request<{id:string;installed:boolean}>(`/api/system/visual-models/${encodeURIComponent(id)}`,{method:"DELETE"}),
  hardware:(refresh=false)=>request<HardwareInfo>(`/api/system/hardware?refresh=${refresh}`),
  autoPlan: (projectId: string, options: Record<string, unknown>) => request<AutoPlan>(`/api/projects/${projectId}/auto-plan`, {
    method: "POST",
    body: JSON.stringify(options),
  }),
  configureModels:(vlmPath:string,mmprojPath:string)=>request<{status:string}>("/api/models/configure",{method:"POST",body:JSON.stringify({vlm_path:vlmPath,mmproj_path:mmprojPath})}),
  testRun: (projectId:string, recipe:Record<string,unknown>, model:Record<string,unknown>, resources:Record<string,unknown>) => request<{command_id:string;state:TestRunState}>(`/api/projects/${projectId}/test-run`, {method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({recipe_snapshot:recipe,model_snapshot:model,effective_resource_configuration:resources})}),
  run: (runId:string) => request<Run>(`/api/runs/${runId}`),
  projectRuns:(projectId:string)=>request<Run[]>(`/api/projects/${projectId}/runs`),
  projectStorage:(projectId:string)=>request<ProjectStorage>(`/api/projects/${projectId}/storage`),
  cleanupProjectStorage:(projectId:string)=>request<{removed_detail_for_runs:string[];usage:ProjectStorage}>(`/api/projects/${projectId}/storage/cleanup`,{method:"POST"}),
  runCommand: (runId:string, action:"pause"|"resume"|"stop") => request<{state:Run}>(`/api/runs/${runId}/${action}`, {method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()}}),
  disableReviewEach:(runId:string)=>request<{state:Run}>(`/api/runs/${runId}/review-each/disable`,{method:"POST"}),
  reviews: (projectId:string) => request<ReviewItem[]>(`/api/projects/${projectId}/review?status=pending`),
  reviewDecision: (id:string,action:"accept"|"edit"|"regenerate"|"skip",body:Record<string,string>={}) => request<{state:ReviewItem}>(`/api/review/${id}/${action}`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify(body)}),
  imageUrl: (projectId:string,path:string) => `${API_ROOT}/api/projects/${projectId}/image?path=${encodeURIComponent(path)}`,
  createRun: (projectId:string, plan:AutoPlan) => request<{state:Run}>(`/api/projects/${projectId}/runs`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({scope_plan:plan.scope,recipe_snapshot:plan.recipe_draft,model_snapshot:plan.model_snapshot,effective_resource_configuration:plan.effective_resource_configuration})}),
  startRun: (runId:string) => request<{state:Run}>(`/api/runs/${runId}/start`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()}}),
  resumeRemaining: (runId:string) => request<{state:Run}>(`/api/runs/${runId}/resume-remaining`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()}}),
  repeatRun: (runId:string) => request<{state:Run}>(`/api/runs/${runId}/repeat`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:"{}"}),
  compareRuns: (left:string,right:string) => request<RunComparison>(`/api/runs/${left}/compare/${right}`),
};
