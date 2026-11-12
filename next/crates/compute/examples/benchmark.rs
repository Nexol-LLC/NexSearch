use nexsearch_compute::{Engine, Kernel};
use std::{hint::black_box, time::Instant};
fn main() {
    let engine = Engine::new(None).unwrap();
    println!("{:?}", engine.info);
    for (count, dimensions, repeats) in [(50, 64, 200), (2000, 256, 50)] {
        let rows: Vec<_> = (0..count)
            .map(|i| {
                (0..dimensions)
                    .map(|j| ((i * 13 + j * 7) % 101) as f64 / 101.)
                    .collect()
            })
            .collect();
        let query = vec![0.125; dimensions];
        for kernel in [Kernel::Scalar, engine.info.kernel] {
            let start = Instant::now();
            for _ in 0..repeats {
                black_box(
                    engine
                        .score_with(kernel, black_box(&query), black_box(&rows))
                        .unwrap(),
                );
            }
            println!(
                "{count} x {dimensions}, {kernel:?}: {:.3} ms/batch",
                start.elapsed().as_secs_f64() * 1000. / repeats as f64
            );
        }
    }
}
