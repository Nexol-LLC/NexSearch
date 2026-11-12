//! Explicit work preferences, not hidden employee surveillance or invented financial data.
use crate::company::{Company, Employee};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct WorkProfile {
    pub industries: Vec<String>,
    pub regions: Vec<String>,
    pub routes: Vec<String>,
    pub available_capacity: u16,
}
#[derive(Debug, Clone, Serialize)]
pub struct Recommendation {
    pub company_id: Uuid,
    pub employee_id: Uuid,
    pub score: u16,
    pub reasons: Vec<String>,
}

pub fn recommend(
    company: &Company,
    employee: &Employee,
    profile: &WorkProfile,
) -> Option<Recommendation> {
    if !company.verified_active() || company.owner_id.is_some() || profile.available_capacity == 0 {
        return None;
    }
    let mut score = 0;
    let mut reasons = vec![];
    if profile.industries.contains(&company.industry) {
        score += 40;
        reasons.push(format!("Знакомая сфера: {}", company.industry));
    }
    if profile.regions.contains(&company.region) {
        score += 25;
        reasons.push(format!("Рабочий регион: {}", company.region));
    }
    let matches = company
        .routes
        .iter()
        .filter(|r| profile.routes.contains(r))
        .count();
    if matches > 0 {
        score += 30;
        reasons.push(format!("Совпадают направления: {matches}"));
    }
    if score == 0 {
        return None;
    }
    Some(Recommendation {
        company_id: company.id,
        employee_id: employee.id,
        score,
        reasons,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::company::*;
    #[test]
    fn suggestions_explain_matching_and_exclude_claimed_or_unknown() {
        let employee = Employee {
            id: Uuid::new_v4(),
            name: "Анна".into(),
            role: Role::Logistician,
            department_id: Uuid::nil(),
        };
        let mut c = Company {
            id: Uuid::new_v4(),
            name: "Пример".into(),
            inn: Inn::try_from("7707083893".to_owned()).unwrap(),
            active: Some(true),
            evidence: Some(Evidence {
                source: "Тестовый источник".into(),
                checked_at: "2026-11-10".into(),
            }),
            industry: "Техника".into(),
            region: "Москва".into(),
            timezone: "Europe/Moscow".into(),
            revenue_rub: None,
            revenue_year: None,
            phone: String::new(),
            email: String::new(),
            routes: vec![],
            owner_id: None,
            stage: String::new(),
        };
        let profile = WorkProfile {
            industries: vec!["Техника".into()],
            available_capacity: 10,
            ..Default::default()
        };
        let result = recommend(&c, &employee, &profile).unwrap();
        assert_eq!(result.score, 40);
        assert_eq!(result.reasons.len(), 1);
        c.owner_id = Some(Uuid::new_v4());
        assert!(recommend(&c, &employee, &profile).is_none());
        c.owner_id = None;
        c.active = None;
        assert!(recommend(&c, &employee, &profile).is_none());
    }
}
