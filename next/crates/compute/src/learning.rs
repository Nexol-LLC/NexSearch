//! Small supervised model for explicit work outcomes. Training is never started silently.
use super::{ComputeError, Kernel, dot_with};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Sample {
    pub features: Vec<f64>,
    pub positive: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Model {
    pub weights: Vec<f64>,
    pub bias: f64,
    pub sample_count: usize,
}

impl Model {
    pub fn train(samples: &[Sample], epochs: usize) -> Result<Self, ComputeError> {
        let dimensions = samples.first().map_or(0, |s| s.features.len());
        if !(8..=10_000).contains(&samples.len())
            || !(1..=64).contains(&dimensions)
            || !(1..=100).contains(&epochs)
            || samples.iter().any(|s| s.features.len() != dimensions)
        {
            return Err(ComputeError::InvalidLength);
        }
        if samples
            .iter()
            .flat_map(|s| &s.features)
            .any(|v| !v.is_finite() || v.abs() > 1.)
        {
            return Err(ComputeError::NonFinite);
        }
        if !samples.iter().any(|s| s.positive) || !samples.iter().any(|s| !s.positive) {
            return Err(ComputeError::InsufficientOutcomes);
        }
        if !samples.iter().skip(1).any(|s| {
            s.features
                .iter()
                .zip(&samples[0].features)
                .any(|(a, b)| (a - b).abs() > 1e-9)
        }) {
            return Err(ComputeError::InsufficientOutcomes);
        }
        let mut model = Self {
            weights: vec![0.; dimensions],
            bias: 0.,
            sample_count: samples.len(),
        };
        let kernel = Kernel::detect();
        for _ in 0..epochs {
            for sample in samples {
                let logit = dot_with(kernel, &model.weights, &sample.features)? + model.bias;
                let prediction = 1. / (1. + (-logit.clamp(-40., 40.)).exp());
                let error = prediction - if sample.positive { 1. } else { 0. };
                for (weight, feature) in model.weights.iter_mut().zip(&sample.features) {
                    *weight -= 0.05 * (error * feature + 0.001 * *weight);
                }
                model.bias -= 0.05 * error;
            }
        }
        Ok(model)
    }
    pub fn predict(&self, features: &[f64]) -> Result<f64, ComputeError> {
        if !self.bias.is_finite() || features.iter().any(|f| !f.is_finite() || f.abs() > 1.) {
            return Err(ComputeError::NonFinite);
        }
        let logit = dot_with(Kernel::detect(), &self.weights, features)? + self.bias;
        Ok(1. / (1. + (-logit.clamp(-40., 40.)).exp()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn explicit_outcomes_train_a_small_predictor() {
        let samples: Vec<_> = (0..40)
            .map(|i| Sample {
                features: vec![if i % 2 == 0 { 1. } else { -1. }, 0.],
                positive: i % 2 == 0,
            })
            .collect();
        let model = Model::train(&samples, 30).unwrap();
        assert!(model.predict(&[1., 0.]).unwrap() > 0.8);
        assert!(model.predict(&[-1., 0.]).unwrap() < 0.2);
        assert_eq!(model.sample_count, 40);
    }
    #[test]
    fn corrupt_models_and_inadequate_history_are_rejected() {
        assert!(Model::train(&[], 1).is_err());
        let samples = vec![
            Sample {
                features: vec![1.],
                positive: true
            };
            8
        ];
        assert_eq!(
            Model::train(&samples, 10).unwrap_err(),
            ComputeError::InsufficientOutcomes
        );
        let model = Model {
            weights: vec![f64::NAN],
            bias: 0.,
            sample_count: 10,
        };
        assert!(model.predict(&[1.]).is_err());
    }
}
