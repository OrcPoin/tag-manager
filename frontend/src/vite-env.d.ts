/// <reference types="vite/client" />

declare module "*.css";

interface Window {
  pywebview?: { api?: {
    select_dataset_folder(initial?: string): Promise<string>;
    select_gguf_file(kind:string,initial?:string):Promise<string>;
    get_desktop_settings(): Promise<{keep_background:boolean;autostart:boolean;notifications:boolean}>;
    set_desktop_settings(values:{keep_background:boolean;autostart:boolean;notifications:boolean}): Promise<{keep_background:boolean;autostart:boolean;notifications:boolean}>;
  } };
}
