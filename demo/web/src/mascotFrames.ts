export type MascotAction =
  "writing" | "calling" | "reporting" | "turbo" | "stamping" | "presenting";

// Measured on the navy desk and its alpha silhouette, excluding moving props.
// [horizontal center, bottom edge, width], in each source's 320px coordinates.
export const deskAnchors: Record<MascotAction, readonly (readonly number[])[]> =
  {
    writing: [
      [173.5, 301, 243],
      [148.5, 301, 245],
      [173, 288, 244],
      [148.5, 288, 245],
    ],
    calling: [
      [173, 302, 246],
      [149.5, 302, 247],
      [172.5, 288, 247],
      [149.5, 288, 247],
    ],
    reporting: [
      [172.5, 302, 245],
      [149, 302, 246],
      [172.5, 288, 247],
      [148.5, 288, 247],
    ],
    turbo: [
      [164, 308, 288],
      [157, 308, 288],
      [164.5, 291, 291],
      [159, 292, 290],
    ],
    stamping: [
      [167, 307, 266],
      [164, 307, 274],
      [166.5, 292, 273],
      [164.5, 292, 273],
    ],
    presenting: [[164, 296, 270]],
  };

export const mascotActions = Object.keys(deskAnchors) as MascotAction[];
export const mascotTarget = { centerX: 160, baseline: 304, deskWidth: 252 };

export function mascotPlacement(action: MascotAction, frame: number) {
  const index = Math.max(
    0,
    Math.min(deskAnchors[action].length - 1, Math.floor(frame)),
  );
  const [centerX, baseline, width] = deskAnchors[action][index];
  const scale = mascotTarget.deskWidth / width;
  return {
    src: `/mascot/${action}-${index + 1}.webp`,
    x: mascotTarget.centerX - centerX * scale,
    y: mascotTarget.baseline - baseline * scale,
    size: 320 * scale,
  };
}

let decoded: Promise<unknown> | undefined;
export function preloadMascot() {
  return (decoded ??= Promise.allSettled(
    mascotActions.flatMap((action) =>
      Array.from({ length: deskAnchors[action].length }, (_, frame) => {
        const image = new Image();
        image.src = mascotPlacement(action, frame).src;
        return image.decode();
      }),
    ),
  ));
}
