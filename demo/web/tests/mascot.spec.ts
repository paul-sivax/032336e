import { expect, test } from "@playwright/test";

test("all twenty-one mascot frames share a stable desk and stage viewport", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 900, height: 1110 });
  await page.goto("/tests/mascot.html");
  await expect(page.locator(".qa-sheet svg")).toHaveCount(21);
  const landmarks = await page
    .locator(".qa-sheet svg image")
    .evaluateAll(async (elements) => {
      return Promise.all(
        elements.map(async (element) => {
          const image = new Image();
          image.src = element.getAttribute("href")!;
          await image.decode();
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = 320;
          const ctx = canvas.getContext("2d")!;
          ctx.drawImage(
            image,
            Number(element.getAttribute("x")),
            Number(element.getAttribute("y")),
            Number(element.getAttribute("width")),
            Number(element.getAttribute("height")),
          );
          const pixels = ctx.getImageData(0, 0, 320, 320).data;
          const rows: number[][] = [];
          const silhouetteRows: number[] = [];
          for (let y = 220; y < 320; y++) {
            const xs: number[] = [];
            let opaque = 0;
            for (let x = 0; x < 320; x++) {
              const p = (y * 320 + x) * 4;
              if (pixels[p + 3] > 128) opaque++;
              if (
                pixels[p + 3] > 220 &&
                pixels[p] > 8 &&
                pixels[p] < 50 &&
                pixels[p + 1] > 20 &&
                pixels[p + 1] < 80 &&
                pixels[p + 2] > 40 &&
                pixels[p + 2] < 130
              )
                xs.push(x);
            }
            if (opaque > 180) silhouetteRows.push(y);
            if (xs.length > 180) rows.push([y, xs[0], xs.at(-1)!]);
          }
          const left = Math.min(...rows.map((r) => r[1])),
            right = Math.max(...rows.map((r) => r[2])) + 1;
          return {
            frame: image.src.split("/").at(-1),
            center: (left + right) / 2,
            bottom: Math.max(...silhouetteRows) + 1,
            width: right - left,
          };
        }),
      );
    });
  await info.attach("desk-landmarks.json", {
    body: JSON.stringify(landmarks),
    contentType: "application/json",
  });
  await page
    .locator(".qa-sheet")
    .screenshot({ path: info.outputPath("aligned-twenty-one-frames.png") });
  await page.getByRole("checkbox").check();
  await page
    .locator(".qa-sheet")
    .screenshot({ path: info.outputPath("original-twenty-one-frames.png") });
  for (const mark of landmarks) {
    expect(Math.abs(mark.center - 160), mark.frame).toBeLessThanOrEqual(1);
    expect(Math.abs(mark.bottom - 304), mark.frame).toBeLessThanOrEqual(1);
    expect(Math.abs(mark.width - 252), mark.frame).toBeLessThanOrEqual(2);
  }
  // The text changes from two lines to one while the artwork stays in place.
  await page.reload();
  await expect(page.locator(".workflow")).toHaveAttribute(
    "data-stage",
    "writing",
  );
  const before = await page.locator(".work-art").boundingBox();
  await expect(page.locator(".workflow")).toHaveAttribute(
    "data-stage",
    "calling",
  );
  const after = await page.locator(".work-art").boundingBox();
  expect(after).toEqual(before);
  const frames = new Set<string>();
  for (let i = 0; i < 10; i++) {
    frames.add(
      (await page.locator(".work-art svg").getAttribute("data-frame"))!,
    );
    await page.waitForTimeout(100);
  }
  expect(frames.size).toBe(4);
});
