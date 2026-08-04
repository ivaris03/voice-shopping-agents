-- Remove spacing that was appropriate between English words but not before Chinese copy.

UPDATE products
SET description = regexp_replace(description, ' ([是以会作为把])', '\1', 'g')
WHERE id::text LIKE '20000000-0000-4000-8000-%';
