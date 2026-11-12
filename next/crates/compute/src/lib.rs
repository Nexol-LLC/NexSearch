#![feature(portable_simd)]
//! Runtime-dispatched numerical scoring. No global target-cpu=native or AVX flags.
use rayon::{ThreadPool, ThreadPoolBuilder, prelude::*};
use serde::Serialize;
pub mod learning;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kernel {
    Scalar,
    Avx2,
    Neon,
}
impl Kernel {
    pub fn supported(self) -> bool {
        match self {
            Self::Scalar => true,
            Self::Avx2 => {
                #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
                {
                    std::arch::is_x86_feature_detected!("avx2")
                }
                #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
                {
                    false
                }
            }
            Self::Neon => {
                #[cfg(target_arch = "aarch64")]
                {
                    std::arch::is_aarch64_feature_detected!("neon")
                }
                #[cfg(not(target_arch = "aarch64"))]
                {
                    false
                }
            }
        }
    }
    pub fn detect() -> Self {
        if Self::Avx2.supported() {
            Self::Avx2
        } else if Self::Neon.supported() {
            Self::Neon
        } else {
            Self::Scalar
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum ComputeError {
    InvalidLength,
    NonFinite,
    UnsupportedKernel,
    InvalidThreadCount,
    PoolUnavailable,
    InsufficientOutcomes,
}

fn scalar(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b).map(|(a, b)| a * b).sum()
}

// The unsafe boundary is limited to target_feature dispatch. All indexing and
// SIMD loads use checked slices; the rest of the application forbids unsafe.
#[allow(unsafe_code)]
mod kernels {
    use super::*;
    use std::simd::{Simd, num::SimdFloat};
    #[inline(always)]
    fn portable(a: &[f64], b: &[f64]) -> f64 {
        let mut acc = Simd::<f64, 4>::splat(0.);
        let count = a.len() / 4 * 4;
        for i in (0..count).step_by(4) {
            acc += Simd::from_slice(&a[i..i + 4]) * Simd::from_slice(&b[i..i + 4]);
        }
        acc.reduce_sum() + super::scalar(&a[count..], &b[count..])
    }
    #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
    #[target_feature(enable = "avx2")]
    unsafe fn avx2(a: &[f64], b: &[f64]) -> f64 {
        portable(a, b)
    }
    #[cfg(target_arch = "aarch64")]
    #[target_feature(enable = "neon")]
    unsafe fn neon(a: &[f64], b: &[f64]) -> f64 {
        portable(a, b)
    }
    pub fn run(kernel: Kernel, a: &[f64], b: &[f64]) -> f64 {
        // SAFETY: callers validate equal lengths. Feature checks are performed
        // here as well, so even an accidentally forced kernel cannot cause SIGILL.
        match kernel {
            #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
            Kernel::Avx2 if Kernel::Avx2.supported() => unsafe { avx2(a, b) },
            #[cfg(target_arch = "aarch64")]
            Kernel::Neon if Kernel::Neon.supported() => unsafe { neon(a, b) },
            _ => super::scalar(a, b),
        }
    }
}

pub fn dot_with(kernel: Kernel, a: &[f64], b: &[f64]) -> Result<f64, ComputeError> {
    if a.len() != b.len() {
        return Err(ComputeError::InvalidLength);
    }
    if !a.iter().chain(b).all(|v| v.is_finite()) {
        return Err(ComputeError::NonFinite);
    }
    if !kernel.supported() {
        return Err(ComputeError::UnsupportedKernel);
    }
    let value = kernels::run(kernel, a, b);
    if !value.is_finite() {
        return Err(ComputeError::NonFinite);
    }
    Ok(value)
}

#[derive(Debug, Clone, Serialize)]
pub struct CpuInfo {
    pub architecture: &'static str,
    pub logical_cpus: usize,
    pub workers: usize,
    pub kernel: Kernel,
}
pub struct Engine {
    pool: ThreadPool,
    pub info: CpuInfo,
}
impl Engine {
    pub fn new(workers: Option<usize>) -> Result<Self, ComputeError> {
        let cpus = std::thread::available_parallelism().map_or(1, usize::from);
        let workers = workers.unwrap_or(cpus.saturating_sub(1).clamp(1, 8));
        if workers == 0 || workers > cpus.min(32) {
            return Err(ComputeError::InvalidThreadCount);
        }
        let pool = ThreadPoolBuilder::new()
            .num_threads(workers)
            .thread_name(|n| format!("nexsearch-cpu-{n}"))
            .build()
            .map_err(|_| ComputeError::PoolUnavailable)?;
        Ok(Self {
            pool,
            info: CpuInfo {
                architecture: std::env::consts::ARCH,
                logical_cpus: cpus,
                workers,
                kernel: Kernel::detect(),
            },
        })
    }
    pub fn score(&self, query: &[f64], rows: &[Vec<f64>]) -> Result<Vec<f64>, ComputeError> {
        self.score_with(self.info.kernel, query, rows)
    }
    pub fn score_with(
        &self,
        kernel: Kernel,
        query: &[f64],
        rows: &[Vec<f64>],
    ) -> Result<Vec<f64>, ComputeError> {
        if !kernel.supported() {
            return Err(ComputeError::UnsupportedKernel);
        }
        if !query.iter().all(|n| n.is_finite()) || rows.iter().flatten().any(|n| !n.is_finite()) {
            return Err(ComputeError::NonFinite);
        }
        if rows.iter().any(|row| row.len() != query.len()) {
            return Err(ComputeError::InvalidLength);
        }
        let calculate = |row: &Vec<f64>| {
            let value = kernels::run(kernel, query, row);
            if value.is_finite() {
                Ok(value)
            } else {
                Err(ComputeError::NonFinite)
            }
        };
        if rows.len() >= 128 && self.info.workers > 1 {
            self.pool
                .install(|| rows.par_iter().map(calculate).collect())
        } else {
            rows.iter().map(calculate).collect()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn every_supported_kernel_matches_scalar_including_tails_and_empty_inputs() {
        for n in 0..=257 {
            let a: Vec<_> = (0..n).map(|i| ((i * 17) % 31) as f64 / 31. - 0.5).collect();
            let b: Vec<_> = (0..n).map(|i| i as f64 * 0.3).collect();
            let expected = dot_with(Kernel::Scalar, &a, &b).unwrap();
            for kernel in [Kernel::Avx2, Kernel::Neon] {
                if kernel.supported() {
                    let actual = dot_with(kernel, &a, &b).unwrap();
                    assert!(
                        (expected - actual).abs() <= 1e-10 * (1. + expected.abs()),
                        "{n} {kernel:?}"
                    );
                }
            }
        }
    }
    #[test]
    fn invalid_input_and_unavailable_architecture_are_safe_errors() {
        assert_eq!(
            dot_with(Kernel::Scalar, &[1.], &[]),
            Err(ComputeError::InvalidLength)
        );
        assert_eq!(
            dot_with(Kernel::Scalar, &[f64::NAN], &[1.]),
            Err(ComputeError::NonFinite)
        );
        assert_eq!(
            dot_with(Kernel::Scalar, &[f64::MAX], &[2.]),
            Err(ComputeError::NonFinite)
        );
        for k in [Kernel::Avx2, Kernel::Neon] {
            if !k.supported() {
                assert_eq!(
                    dot_with(k, &[1.], &[1.]),
                    Err(ComputeError::UnsupportedKernel)
                );
            }
        }
    }
    #[test]
    fn rayon_and_single_worker_results_agree_in_original_order() {
        let single = Engine::new(Some(1)).unwrap();
        let automatic = Engine::new(None).unwrap();
        let rows: Vec<_> = (0..256).map(|i| vec![i as f64, 2., 3., 4., 5.]).collect();
        let q = [1., 2., 3., 4., 5.];
        let expected = single.score_with(Kernel::Scalar, &q, &rows).unwrap();
        assert_eq!(expected, automatic.score(&q, &rows).unwrap());
        assert!(Engine::new(Some(0)).is_err());
    }
}
