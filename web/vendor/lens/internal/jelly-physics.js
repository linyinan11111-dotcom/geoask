export const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

/** A bounded-timestep damped spring; the target may change at any time. */
export class Spring {
  constructor(value = 0) { this.value = value; this.velocity = 0; this.target = value; }
  step(dt, stiffness = 220, damping = 20) {
    let remaining = Math.min(Math.max(dt, 0), .08);
    while (remaining > 0) {
      const h = Math.min(remaining, 1 / 180);
      this.velocity += ((this.target - this.value) * stiffness - this.velocity * damping) * h;
      this.value += this.velocity * h;
      remaining -= h;
    }
    if (Math.abs(this.velocity) < .025 && Math.abs(this.target - this.value) < .005) {
      this.value = this.target; this.velocity = 0;
    }
    return this.value;
  }
  snap(value = this.target) { this.value = this.target = value; this.velocity = 0; }
  get moving() { return Math.abs(this.velocity) > .025 || Math.abs(this.target - this.value) > .005; }
}

/** Stretch along motion, compress across it, preserving area apart from lift. */
export function jellyMatrix(stretch, angle, lift = 1) {
  const along = 1 + clamp(stretch, -.20, .34), across = 1 / along;
  const c = Math.cos(angle), s = Math.sin(angle);
  return [
    (along * c * c + across * s * s) * lift,
    (along - across) * c * s * lift,
    (along - across) * c * s * lift,
    (along * s * s + across * c * c) * lift
  ];
}

export function boundDrag(x, y, box, viewport, padding = 16) {
  return {
    x: clamp(x, padding - box.left, viewport.width - padding - box.right),
    y: clamp(y, padding - box.top, viewport.height - padding - box.bottom)
  };
}

/** A toggle stretches along its rail without ever crossing the rail's inset. */
export function togglePose(position, travel, size, pressure=0, stretch=0) {
  const scaleX=1+clamp(pressure*.10+stretch,0,Math.min(.18,travel/Math.max(size,1)));
  const extra=(scaleX-1)*size/2;
  return {x:clamp(position,extra,Math.max(extra,travel-extra)),scaleX,scaleY:1/scaleX};
}
