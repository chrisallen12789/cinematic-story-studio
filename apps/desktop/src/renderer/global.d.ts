import type { CinematicStoryDesktopApi } from "../shared/desktop-api";

declare global {
  interface Window {
    readonly cinematicStory: CinematicStoryDesktopApi;
  }
}

export {};
